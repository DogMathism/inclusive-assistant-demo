from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc
from uuid import UUID
from datetime import datetime
from typing import List, Dict
import json

from .db import Base, engine, SessionLocal
from . import models, schemas
from .services.metrics import compute_block_metrics
from .services.profiling import normalize_time
from .services.indices import calc_overload_index, calc_readiness_index
from .services.recommendations import make_recommendation

app = FastAPI()
Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ------------------------
# WebSocket онлайн-доска
# ------------------------
board_connections: Dict[str, List[WebSocket]] = {}
board_states: Dict[str, List[dict]] = {}

@app.websocket("/ws/board/{lesson_block_id}")
async def board_ws(websocket: WebSocket, lesson_block_id: str):
    await websocket.accept()

    if lesson_block_id not in board_connections:
        board_connections[lesson_block_id] = []
        board_states[lesson_block_id] = []

    board_connections[lesson_block_id].append(websocket)

    # отправляем текущее состояние доски
    for action in board_states[lesson_block_id]:
        await websocket.send_json(action)

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "clear":
                board_states[lesson_block_id] = []
            else:
                board_states[lesson_block_id].append(data)
            for conn in board_connections[lesson_block_id]:
                if conn != websocket:
                    await conn.send_json(data)
    except WebSocketDisconnect:
        board_connections[lesson_block_id].remove(websocket)

# ------------------------
# Эндпоинты MVP
# ------------------------
@app.post("/api/events")
def create_event(data: schemas.EventCreate, db: Session = Depends(get_db)):
    event = models.TaskEvent(
        student_id=data.student_id,
        task_id=data.task_id,
        lesson_block_id=data.lesson_block_id,
        event_type=data.event_type,
        is_correct=data.is_correct
    )
    db.add(event)
    db.commit()
    return {"status": "ok"}

@app.post("/api/lesson-blocks/start")
def start_block(student_id: UUID, db: Session = Depends(get_db)):
    block = models.LessonBlock(student_id=student_id)
    db.add(block)
    db.commit()
    db.refresh(block)
    return {"lesson_block_id": str(block.id)}

@app.post("/api/lesson-blocks/{block_id}/finish")
def finish_block(block_id, db: Session = Depends(get_db)):
    block = db.query(models.LessonBlock).filter(models.LessonBlock.id == block_id).first()
    if not block:
        raise HTTPException(404, "lesson block not found")

    events = db.query(models.TaskEvent).filter(models.TaskEvent.lesson_block_id == block_id).all()
    if not events:
        raise HTTPException(400, "no events")

    metrics = compute_block_metrics(events)

    profile = db.query(models.NeuroProfile).filter(models.NeuroProfile.student_id == block.student_id).first()
    if not profile:
        raise HTTPException(400, "neuro profile not found")

    norm_time = normalize_time(metrics["duration"], profile.processing_speed)
    sensory_mismatch = abs(0.5 - (1 - profile.sensory_sensitivity))
    fatigue_proxy = min(metrics["duration"]/1800, 1.0)

    overload = calc_overload_index(
        accuracy=metrics["accuracy"],
        norm_time=norm_time,
        skip_rate=metrics["skip_rate"],
        sensory_mismatch=sensory_mismatch
    )
    readiness = calc_readiness_index(
        accuracy=metrics["accuracy"],
        norm_time=norm_time,
        fatigue_proxy=fatigue_proxy
    )

    idx = models.BlockIndex(
        lesson_block_id=block.id,
        overload_index=overload,
        readiness_index=readiness
    )
    block.finished_at = datetime.utcnow()
    db.add(idx)
    db.commit()

    recommendation = make_recommendation(overload, readiness)
    return {"overload_index": overload, "readiness_index": readiness, "recommendation": recommendation}

@app.get("/api/teacher/students/{student_id}/dashboard")
def teacher_dashboard(student_id: UUID, db: Session = Depends(get_db)):
    student = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not student:
        raise HTTPException(404, "student not found")

    last_block = db.query(models.LessonBlock)\
        .filter(models.LessonBlock.student_id == student_id)\
        .order_by(desc(models.LessonBlock.started_at))\
        .first()

    last_indices = None
    recommendation = None
    if last_block:
        last_indices = db.query(models.BlockIndex).filter(models.BlockIndex.lesson_block_id == last_block.id).first()
        if last_indices:
            recommendation = make_recommendation(last_indices.overload_index, last_indices.readiness_index)

   history = db.query(
        models.LessonBlock.id,
        models.BlockIndex.overload_index,
        models.BlockIndex.readiness_index,
        models.LessonBlock.started_at
    ).join(
        models.BlockIndex, models.BlockIndex.lesson_block_id == models.LessonBlock.id
    ).filter(models.LessonBlock.student_id == student_id)\
     .order_by(models.LessonBlock.started_at).all()

    return {
        "student": {"id": str(student.id), "full_name": student.full_name},
        "last_block": None if not last_indices else {
            "lesson_block_id": str(last_block.id),
            "overload_index": last_indices.overload_index,
            "readiness_index": last_indices.readiness_index
        },
        "recommendation": recommendation,
        "history": [
            {
                "lesson_block_id": str(h.id),
                "started_at": h.started_at.isoformat(),
                "overload_index": h.overload_index,
                "readiness_index": h.readiness_index
            } for h in history
        ]
    }

# ------------------------
# Demo страницы
# ------------------------
@app.get("/demo/student", response_class=HTMLResponse)
def demo_student_page():
    return """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>Student demo</title>
<style>
body { font-family: Arial; margin:20px; }
button{padding:6px 12px; margin:2px;}
canvas{border:1px solid #ccc; margin-top:10px;}
</style>
</head>
<body>
<h2>Демо: ученик</h2>
<p>Student ID:</p>
<input id="studentId" value="11111111-1111-1111-1111-111111111111"/>
<p>Task ID (любое UUID):</p>
<input id="taskId" value="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"/>

<button onclick="startBlock()">Начать блок</button><br/>
<p>Lesson block ID: <span id="blockId">—</span></p>

<button onclick="sendAnswer(true)">Ответил правильно</button>
<button onclick="sendAnswer(false)">Ответил неправильно</button>
<button onclick="sendSkip()">Пропустить</button><br/>

<button onclick="finishBlock()">Завершить блок</button>

<h3>Онлайн-доска</h3>
<button onclick="setTool('pen')">Карандаш</button>
<button onclick="setTool('eraser')">Ластик</button>
<button onclick="clearBoard()">Очистить</button><br/>
<canvas id="board" width="600" height="400"></canvas>

<div id="result" style="margin-top:15px;"></div>

<script>
let currentBlockId=null;
let canvas=document.getElementById("board");
let ctx=canvas.getContext("2d");
let drawing=false; let tool='pen'; let ws=null;

function setTool(t){tool=t;}
function startBlock(){
    const studentId=document.getElementById("studentId").value;
    fetch("/api/lesson-blocks/start?student_id="+studentId,{method:"POST"})
    .then(r=>r.json()).then(data=>{
        currentBlockId=data.lesson_block_id;
        document.getElementById("blockId").innerText=currentBlockId;
        connectBoard();
    });
}
function sendAnswer(isCorrect){
    if(!currentBlockId){alert("Сначала начните блок");return;}
    const studentId=document.getElementById("studentId").value;
    const taskId=document.getElementById("taskId").value;
    fetch("/api/events",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({student_id:studentId,task_id:taskId,lesson_block_id:currentBlockId,event_type:"answer",is_correct:isCorrect})});
}
function sendSkip(){
    if(!currentBlockId){alert("Сначала начните блок");return;}
    const studentId=document.getElementById("studentId").value;
    const taskId=document.getElementById("taskId").value;
    fetch("/api/events",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({student_id:studentId,task_id:taskId,lesson_block_id:currentBlockId,event_type:"skip"})});
}
function finishBlock(){
    fetch("/api/lesson-blocks/"+currentBlockId+"/finish",{method:"POST"}).then(r=>r.json()).then(data=>{
        document.getElementById("result").innerHTML=
            "<b>Блок завершён</b><br>Перегрузка: "+data.overload_index.toFixed(2)+
            "<br>Готовность: "+data.readiness_index.toFixed(2)+
            "<br>Рекомендация: "+data.recommendation.action;
        currentBlockId=null;
    });
}

canvas.onmousedown=e=>{drawing=true; sendDraw("start",e);}
canvas.onmouseup=e=>{drawing=false; sendDraw("end",e);}
canvas.onmousemove=e=>{if(drawing) sendDraw("draw",e);}

function sendDraw(type,e){
    if(!ws) return;
    const rect=canvas.getBoundingClientRect();
    const data={type:type,x:e.clientX-rect.left,y:e.clientY-rect.top,tool:tool};
    drawAction(data,true);
    ws.send(JSON.stringify(data));
}
function drawAction(data,self){
    if(data.type==="start"||data.type==="draw"){
        ctx.strokeStyle=(data.tool==="eraser")?"#fff":"#000";
        ctx.lineWidth=(data.tool==="eraser")?10:2;
        ctx.lineTo(data.x,data.y);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(data.x,data.y);
    }
    if(data.type==="end") ctx.beginPath();
    if(data.type==="clear") ctx.clearRect(0,0,canvas.width,canvas.height);
}
function clearBoard(){
    ctx.clearRect(0,0,canvas.width,canvas.height);
    if(ws) ws.send(JSON.stringify({type:"clear"}));
}
function connectBoard(){
    ws=new WebSocket("ws://localhost:8000/ws/board/"+currentBlockId);
    ws.onmessage=event=>{drawAction(JSON.parse(event.data),false);}
}
</script>
</body>
</html>
"""

# ------------------------
@app.get("/demo/teacher", response_class=HTMLResponse)
def demo_teacher_page():
    return """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>Teacher demo</title>
<style>
body{font-family:Arial;margin:20px;}
canvas{border:1px solid #ccc;margin-top:10px;}
button{padding:6px 12px;margin:2px;}
</style>
</head>
<body>
<h2>Демо: преподаватель</h2>
<p>Student ID:</p>
<input id="studentId" value="11111111-1111-1111-1111-111111111111"/>
<button onclick="loadDashboard()">Загрузить дашборд</button>

<div id="result" style="margin-top:15px;"></div>

<h3>Доска ученика</h3>
<canvas id="teacherBoard" width="600" height="400"></canvas>

<script>
let tCanvas=document.getElementById("teacherBoard");
let tCtx=tCanvas.getContext("2d");
let tWs=null;
let lessonBlockId=null;

function loadDashboard(){
    const studentId=document.getElementById("studentId").value;
    fetch("/api/teacher/students/"+studentId+"/dashboard")
    .then(r=>r.json())
    .then(data=>{
        let html="<b>Ученик:</b> "+data.student.full_name+"<br><br>";
        if(data.last_block){
            lessonBlockId=data.last_block.lesson_block_id;
            html+="<b>Индекс перегрузки:</b> "+data.last_block.overload_index.toFixed(2)+"<br>";
            html+="<b>Индекс готовности:</b> "+data.last_block.readiness_index.toFixed(2)+"<br><br>";
            if(data.recommendation) html+="<b>Рекомендация:</b> "+data.recommendation.text+"<br>";
            connectTeacherBoard(lessonBlockId);
        }else html+="Нет блоков<br>";
        html+="<b>История блоков:</b><br>";
        data.history.forEach(h=>{
            html+=h.started_at.substring(0,19)+" | перегрузка="+h.overload_index.toFixed(2)+
                  " | готовность="+h.readiness_index.toFixed(2)+"<br>";
        });
        document.getElementById("result").innerHTML=html;
    });
}

function connectTeacherBoard(blockId){
    tWs=new WebSocket("ws://localhost:8000/ws/board/"+blockId);
    tWs.onmessage=event=>{
        const data=JSON.parse(event.data);
        drawTeacherAction(data);
    };
}

function drawTeacherAction(data){
    if(data.type==="start"||data.type==="draw"){
        tCtx.strokeStyle=(data.tool==="eraser")?"#fff":"#000";
        tCtx.lineWidth=(data.tool==="eraser")?10:2;
        tCtx.lineTo(data.x,data.y);
        tCtx.stroke();
        tCtx.beginPath();
        tCtx.moveTo(data.x,data.y);
    }
    if(data.type==="end") tCtx.beginPath();
    if(data.type==="clear") tCtx.clearRect(0,0,tCanvas.width,tCanvas.height);
}
</script>
</body>
</html>
"""