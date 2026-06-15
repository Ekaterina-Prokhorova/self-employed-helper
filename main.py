import os
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import sessionmaker, declarative_base, Session, relationship
from datetime import datetime, timedelta
from pydantic import BaseModel
from typing import Optional, List
import httpx
import json

# --- НАСТРОЙКИ ---
DATABASE_URL = "sqlite:///./self_employed.db"
YANDEX_API_KEY = "AQVNzzFXrf75BpE4gW4qcPAJGonrTOiFSbix34Zs"
YANDEX_FOLDER_ID = "b1gbmpbrqpngq65j2ok9"
PROXY_URL = "https://plain-cake-6869.fer043948.workers.dev"

# --- БАЗА ДАННЫХ ---
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    incomes = relationship("Income", back_populates="owner")
    reminders = relationship("Reminder", back_populates="owner")
    chat_messages = relationship("ChatMessage", back_populates="owner")

class Income(Base):
    __tablename__ = "incomes"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"))
    date = Column(String)
    description = Column(String)
    amount = Column(Float)
    client_type = Column(String) # individual / legal
    tax_rate = Column(Integer)   # 4 or 6
    created_at = Column(DateTime, default=datetime.utcnow)
    owner = relationship("User", back_populates="incomes")

class Reminder(Base):
    __tablename__ = "reminders"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"))
    date = Column(String)
    amount = Column(Float)
    description = Column(String)
    done = Column(Boolean, default=False)
    owner = relationship("User", back_populates="reminders")

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"))
    role = Column(String) # user / assistant
    text = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    owner = relationship("User", back_populates="chat_messages")

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- МОДЕЛИ PYDANTIC ---
class UserCreate(BaseModel):
    email: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class IncomeCreate(BaseModel):
    date: str
    description: str
    amount: float
    client_type: str

class ReminderCreate(BaseModel):
    date: str
    amount: float
    description: str

class ChatRequest(BaseModel):
    text: str

# --- ПРИЛОЖЕНИЕ ---
app = FastAPI()

# Разрешаем запросы с вашего сайта на GitHub Pages
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
from passlib.context import CryptContext
from jose import jwt, JWTError

SECRET_KEY = "super-secret-key-change-it-later"
ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain, hashed):
    return pwd_context.verify(plain, hashed)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=30)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str, db: Session = Depends(get_db)):
    # Упрощенная проверка для прототипа (в реальном проекте нужна полная проверка JWT)
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None: raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    user = db.query(User).filter(User.email == email).first()
    if user is None: raise HTTPException(status_code=401, detail="User not found")
    return user

# --- ЭНДПОИНТЫ ---

@app.post("/register")
async def register(user: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == user.email).first():
        raise HTTPException(400, "Email already registered")
    hashed_pw = get_password_hash(user.password)
    db_user = User(email=user.email, hashed_password=hashed_pw)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    token = create_access_token({"sub": user.email})
    return {"access_token": token, "token_type": "bearer"}

@app.post("/login")
async def login(email: str, password: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(401, "Incorrect email or password")
    token = create_access_token({"sub": user.email})
    return {"access_token": token, "token_type": "bearer"}

@app.get("/incomes")
async def get_incomes(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(Income).filter(Income.owner_id == current_user.id).all()

@app.post("/incomes")
async def add_income(income: IncomeCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    tax_rate = 4 if income.client_type == "individual" else 6
    db_income = Income(**income.dict(), owner_id=current_user.id, tax_rate=tax_rate)
    db.add(db_income)
    db.commit()
    db.refresh(db_income)
    return db_income

@app.delete("/incomes/{income_id}")
async def delete_income(income_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    income = db.query(Income).filter(Income.id == income_id, Income.owner_id == current_user.id).first()
    if not income: raise HTTPException(404, "Not found")
    db.delete(income)
    db.commit()
    return {"message": "Deleted"}

@app.get("/reminders")
async def get_reminders(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(Reminder).filter(Reminder.owner_id == current_user.id).all()

@app.post("/reminders")
async def add_reminder(reminder: ReminderCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    db_reminder = Reminder(**reminder.dict(), owner_id=current_user.id)
    db.add(db_reminder)
    db.commit()
    db.refresh(db_reminder)
    return db_reminder

@app.patch("/reminders/{reminder_id}/toggle")
async def toggle_reminder(reminder_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reminder = db.query(Reminder).filter(Reminder.id == reminder_id, Reminder.owner_id == current_user.id).first()
    if not reminder: raise HTTPException(404, "Not found")
    reminder.done = not reminder.done
    db.commit()
    return {"done": reminder.done}

@app.post("/chat/send")
async def chat_send(req: ChatRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Сохраняем сообщение пользователя
    user_msg = ChatMessage(owner_id=current_user.id, role="user", text=req.text)
    db.add(user_msg)
    db.commit()
    
    # История для контекста
    history = db.query(ChatMessage).filter(ChatMessage.owner_id == current_user.id).order_by(ChatMessage.created_at.desc()).limit(5).all()
    history.reverse()
    
    messages_payload = [{"role": m.role, "text": m.text} for m in history]
    
    # Запрос к YandexGPT через прокси
    system_prompt = "Ты эксперт по самозанятости в России. Отвечай кратко и по делу."
    final_messages = [{"role": "system", "text": system_prompt}] + messages_payload
    
    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
        "completionOptions": {"stream": False, "temperature": 0.6, "maxTokens": 1000},
        "messages": final_messages
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(PROXY_URL, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            ai_text = response.json()["result"]["alternatives"][0]["message"]["text"]
    except Exception as e:
        ai_text = f"Ошибка AI: {str(e)}"
        
    # Сохраняем ответ
    ai_msg = ChatMessage(owner_id=current_user.id, role="assistant", text=ai_text)
    db.add(ai_msg)
    db.commit()
    
    return {"reply": ai_text}

@app.get("/stats/summary")
async def get_stats(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    incomes = db.query(Income).filter(Income.owner_id == current_user.id).all()
    now = datetime.utcnow()
    
    # Простая статистика за текущий месяц
    month_incomes = [i for i in incomes if datetime.fromisoformat(i.date).month == now.month and datetime.fromisoformat(i.date).year == now.year]
    total = sum(i.amount for i in month_incomes)
    tax = sum(i.amount * i.tax_rate / 100 for i in month_incomes)
    
    return {
        "month_total": total,
        "month_tax": tax,
        "month_net": total - tax,
        "month_count": len(month_incomes),
        "year_total": sum(i.amount for i in incomes if datetime.fromisoformat(i.date).year == now.year),
        "year_limit_remaining": 2400000 - sum(i.amount for i in incomes if datetime.fromisoformat(i.date).year == now.year)
    }