# main.py
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional, Dict, Set
import uuid
import json
import os
import shutil
import hashlib
import time
import datetime
from jose import JWTError, jwt
import sqlite3
import requests
from threading import Lock, Thread
import schedule

# Configurações
SECRET_KEY = "ufraplus_secret_key_change_this_in_production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 dias
DATABASE_FILE = "ufraplus.db"
UPLOADS_DIR = "uploads"
PEER_SERVERS = set()  # Endereços de outros servidores

# Configurar estrutura de pastas
os.makedirs(UPLOADS_DIR, exist_ok=True)

# Configurar banco de dados SQLite
def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# Inicializar banco de dados
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Criar tabelas se não existirem
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        username TEXT UNIQUE,
        password TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS posts (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        username TEXT,
        content TEXT,
        image TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS likes (
        id TEXT PRIMARY KEY,
        post_id TEXT,
        user_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(post_id, user_id),
        FOREIGN KEY (post_id) REFERENCES posts (id),
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS comments (
        id TEXT PRIMARY KEY,
        post_id TEXT,
        user_id TEXT,
        username TEXT,
        text TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (post_id) REFERENCES posts (id),
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS servers (
        id TEXT PRIMARY KEY,
        url TEXT UNIQUE,
        last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    conn.commit()
    conn.close()

init_db()

# Iniciar aplicação FastAPI
app = FastAPI()

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Montar diretório de uploads
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Sincronização entre servidores
server_lock = Lock()
last_sync_time = time.time()

# Modelos Pydantic
class UserCreate(BaseModel):
    username: str
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str
    username: str

class PostCreate(BaseModel):
    content: str
    image: Optional[str] = None

class CommentCreate(BaseModel):
    post_id: str
    comment: str

class LikeCreate(BaseModel):
    post_id: str

class ServerRegister(BaseModel):
    server_url: str

class SyncData(BaseModel):
    posts: List[Dict]
    likes: List[Dict]
    comments: List[Dict]
    timestamp: float

# Funções de autenticação
def get_password_hash(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain_password, hashed_password):
    return get_password_hash(plain_password) == hashed_password

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Não autenticado")
    
    try:
        token = authorization.split(" ")[1]
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Token inválido")
        
        # Verificar se o usuário existe
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, username FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        conn.close()
        
        if user is None:
            raise HTTPException(status_code=401, detail="Usuário não encontrado")
        
        return {"id": user["id"], "username": user["username"]}
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Erro de autenticação: {str(e)}")

# Funções para sincronização
def load_peer_servers():
    global PEER_SERVERS
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT url FROM servers WHERE url != ?", (os.environ.get("MY_SERVER_URL", "http://localhost:8000"),))
    servers = cursor.fetchall()
    conn.close()
    
    PEER_SERVERS = {server["url"] for server in servers}

def broadcast_to_peers(endpoint, data):
    load_peer_servers()
    for peer in PEER_SERVERS:
        try:
            requests.post(f"{peer}{endpoint}", json=data, timeout=2)
        except:
            pass  # Ignorar falhas, a sincronização completa corrigirá depois

def sync_with_peers():
    global last_sync_time
    
    with server_lock:
        current_time = time.time()
        
        # Preparar dados para sincronização
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Obter posts desde a última sincronização
        cursor.execute("SELECT * FROM posts WHERE created_at > datetime(?)", 
                     (datetime.datetime.fromtimestamp(last_sync_time).isoformat(),))
        posts = [dict(row) for row in cursor.fetchall()]
        
        # Obter likes desde a última sincronização
        cursor.execute("SELECT * FROM likes WHERE created_at > datetime(?)", 
                     (datetime.datetime.fromtimestamp(last_sync_time).isoformat(),))
        likes = [dict(row) for row in cursor.fetchall()]
        
        # Obter comentários desde a última sincronização
        cursor.execute("SELECT * FROM comments WHERE created_at > datetime(?)", 
                     (datetime.datetime.fromtimestamp(last_sync_time).isoformat(),))
        comments = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        sync_data = {
            "posts": posts,
            "likes": likes,
            "comments": comments,
            "timestamp": current_time
        }
        
        # Enviar dados para outros servidores
        load_peer_servers()
        for peer in PEER_SERVERS:
            try:
                requests.post(f"{peer}/sync", json=sync_data, timeout=5)
            except:
                pass  # Ignorar falhas
        
        last_sync_time = current_time

def ping_servers():
    load_peer_servers()
    my_url = os.environ.get("MY_SERVER_URL", "http://localhost:8000")
    
    # Atualizar último acesso para servidores ativos
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Verificar servidores
    servers_to_remove = set()
    for peer in PEER_SERVERS:
        try:
            response = requests.get(f"{peer}/heartbeat", timeout=3)
            if response.status_code == 200:
                cursor.execute(
                    "UPDATE servers SET last_seen = datetime('now') WHERE url = ?",
                    (peer,)
                )
            else:
                servers_to_remove.add(peer)
        except:
            servers_to_remove.add(peer)
    
    # Remover servidores inativos (não vistos por mais de 10 minutos)
    cursor.execute(
        "DELETE FROM servers WHERE last_seen < datetime('now', '-10 minutes')"
    )
    
    conn.commit()
    conn.close()
    
    # Remover do conjunto de servidores ativos
    PEER_SERVERS.difference_update(servers_to_remove)

def schedule_tasks():
    schedule.every(30).seconds.do(ping_servers)
    schedule.every(2).minutes.do(sync_with_peers)
    
    while True:
        schedule.run_pending()
        time.sleep(1)

# Iniciar thread de tarefas
task_thread = Thread(target=schedule_tasks, daemon=True)
task_thread.start()

# Endpoints de autenticação
@app.post("/register")
async def register(user: UserCreate):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Verificar se o nome de usuário já existe
    cursor.execute("SELECT id FROM users WHERE username = ?", (user.username,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Nome de usuário já existe")
    
    # Criar o usuário
    user_id = str(uuid.uuid4())
    hashed_password = get_password_hash(user.password)
    
    cursor.execute(
        "INSERT INTO users (id, username, password) VALUES (?, ?, ?)",
        (user_id, user.username, hashed_password)
    )
    
    conn.commit()
    conn.close()
    
    return {"status": "success", "message": "Usuário registrado com sucesso"}

@app.post("/login")
async def login(user: UserLogin):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, username, password FROM users WHERE username = ?", (user.username,))
    db_user = cursor.fetchone()
    conn.close()
    
    if not db_user or not verify_password(user.password, db_user["password"]):
        raise HTTPException(status_code=401, detail="Nome de usuário ou senha incorretos")
    
    access_token = create_access_token({"sub": db_user["id"]})
    return {"status": "success", "token": access_token, "username": db_user["username"]}

# Endpoints de posts
@app.post("/post")
async def create_post(
    content: str = Form(...),
    image: Optional[UploadFile] = File(None),
    current_user: dict = Depends(get_current_user)
):
    post_id = str(uuid.uuid4())
    image_url = None
    
    if image:
        image_name = f"{post_id}_{image.filename}"
        image_path = os.path.join(UPLOADS_DIR, image_name)
        
        with open(image_path, "wb") as f:
            shutil.copyfileobj(image.file, f)
        
        image_url = f"/uploads/{image_name}"
    
    # Salvar post no banco de dados
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "INSERT INTO posts (id, user_id, username, content, image) VALUES (?, ?, ?, ?, ?)",
        (post_id, current_user["id"], current_user["username"], content, image_url)
    )
    
    conn.commit()
    conn.close()
    
    # Sincronizar com outros servidores
    post_data = {
        "id": post_id,
        "user_id": current_user["id"],
        "username": current_user["username"],
        "content": content,
        "image": image_url,
        "created_at": datetime.datetime.now().isoformat()
    }
    broadcast_to_peers("/post_sync", {"post": post_data})
    
    return {"status": "success", "post_id": post_id}

@app.get("/timeline")
async def get_timeline(
    page: int = 1, 
    per_page: int = 10,
    current_user: dict = Depends(get_current_user)
):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Calcular offset para paginação
    offset = (page - 1) * per_page
    
    # Buscar posts com paginação
    cursor.execute("""
        SELECT p.id, p.user_id, p.username, p.content, p.image, p.created_at
        FROM posts p
        ORDER BY p.created_at DESC
        LIMIT ? OFFSET ?
    """, (per_page, offset))
    
    posts_rows = cursor.fetchall()
    posts = []
    
    for post in posts_rows:
        post_dict = dict(post)
        post_id = post_dict["id"]
        
        # Buscar curtidas para o post
        cursor.execute("SELECT COUNT(*) FROM likes WHERE post_id = ?", (post_id,))
        likes_count = cursor.fetchone()[0]
        
        # Verificar se o usuário atual curtiu o post
        cursor.execute("SELECT id FROM likes WHERE post_id = ? AND user_id = ?", 
                     (post_id, current_user["id"]))
        liked_by_me = bool(cursor.fetchone())
        
        # Buscar comentários para o post
        cursor.execute("""
            SELECT c.id, c.user_id, c.username, c.text, c.created_at
            FROM comments c
            WHERE c.post_id = ?
            ORDER BY c.created_at ASC
        """, (post_id,))
        comments = [dict(row) for row in cursor.fetchall()]
        
        post_dict["likes"] = likes_count
        post_dict["liked_by_me"] = liked_by_me
        post_dict["comments"] = comments
        posts.append(post_dict)
    
    conn.close()
    
    return {"posts": posts}

@app.post("/like")
async def like_post(
    like_data: LikeCreate,
    current_user: dict = Depends(get_current_user)
):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Verificar se o post existe
    cursor.execute("SELECT id FROM posts WHERE id = ?", (like_data.post_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Post não encontrado")
    
    like_id = str(uuid.uuid4())
    
    try:
        # Tentar adicionar curtida
        cursor.execute(
            "INSERT INTO likes (id, post_id, user_id) VALUES (?, ?, ?)",
            (like_id, like_data.post_id, current_user["id"])
        )
        conn.commit()
        
        # Sincronizar com outros servidores
        like_data = {
            "id": like_id,
            "post_id": like_data.post_id,
            "user_id": current_user["id"],
            "created_at": datetime.datetime.now().isoformat()
        }
        broadcast_to_peers("/like_sync", {"like": like_data})
        
        return {"status": "success"}
    except sqlite3.IntegrityError:
        # Usuário já curtiu o post, então descurtir
        cursor.execute(
            "DELETE FROM likes WHERE post_id = ? AND user_id = ?",
            (like_data.post_id, current_user["id"])
        )
        conn.commit()
        
        # Sincronizar a remoção da curtida
        broadcast_to_peers("/unlike_sync", {
            "post_id": like_data.post_id,
            "user_id": current_user["id"]
        })
        
        return {"status": "success"}
    finally:
        conn.close()

@app.post("/comment")
async def add_comment(
    comment_data: CommentCreate,
    current_user: dict = Depends(get_current_user)
):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Verificar se o post existe
    cursor.execute("SELECT id FROM posts WHERE id = ?", (comment_data.post_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Post não encontrado")
    
    comment_id = str(uuid.uuid4())
    
    cursor.execute(
        "INSERT INTO comments (id, post_id, user_id, username, text) VALUES (?, ?, ?, ?, ?)",
        (comment_id, comment_data.post_id, current_user["id"], current_user["username"], comment_data.comment)
    )
    
    conn.commit()
    conn.close()
    
    # Sincronizar com outros servidores
    comment_data_sync = {
        "id": comment_id,
        "post_id": comment_data.post_id,
        "user_id": current_user["id"],
        "username": current_user["username"],
        "text": comment_data.comment,
        "created_at": datetime.datetime.now().isoformat()
    }
    broadcast_to_peers("/comment_sync", {"comment": comment_data_sync})
    
    return {"status": "success"}

# Endpoints para sincronização entre servidores
@app.post("/register_server")
async def register_server(server: ServerRegister):
    my_url = os.environ.get("MY_SERVER_URL", "http://localhost:8000")
    
    if server.server_url == my_url:
        return {"status": "ignored", "message": "Cannot register self"}
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    server_id = str(uuid.uuid4())
    
    try:
        cursor.execute(
            "INSERT INTO servers (id, url) VALUES (?, ?)",
            (server_id, server.server_url)
        )
        conn.commit()
        
        # Adicionar ao conjunto de servidores
        PEER_SERVERS.add(server.server_url)
        
        # Broadcast para outros servidores conhecidos
        broadcast_to_peers("/register_server", {"server_url": server.server_url})
        
        return {"status": "success", "message": "Servidor registrado"}
    except sqlite3.IntegrityError:
        # Servidor já registrado, atualizar timestamp
        cursor.execute(
            "UPDATE servers SET last_seen = datetime('now') WHERE url = ?",
            (server.server_url,)
        )
        conn.commit()
        return {"status": "success", "message": "Registro de servidor atualizado"}
    finally:
        conn.close()

@app.post("/sync")
async def sync_data(data: SyncData):
    with server_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Processar posts
        for post in data.posts:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO posts (id, user_id, username, content, image, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (post["id"], post["user_id"], post["username"], post["content"], post.get("image"), post["created_at"])
                )
            except:
                pass  # Ignorar erros
        
        # Processar curtidas
        for like in data.likes:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO likes (id, post_id, user_id, created_at) VALUES (?, ?, ?, ?)",
                    (like["id"], like["post_id"], like["user_id"], like["created_at"])
                )
            except:
                pass  # Ignorar erros
        
        # Processar comentários
        for comment in data.comments:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO comments (id, post_id, user_id, username, text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (comment["id"], comment["post_id"], comment["user_id"], comment["username"], comment["text"], comment["created_at"])
                )
            except:
                pass  # Ignorar erros
        
        conn.commit()
        conn.close()
        
        return {"status": "success"}

@app.post("/post_sync")
async def sync_post(data: dict):
    post = data.get("post")
    if not post:
        return {"status": "error", "message": "Dados inválidos"}
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO posts (id, user_id, username, content, image, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (post["id"], post["user_id"], post["username"], post["content"], post.get("image"), post["created_at"])
        )
        conn.commit()
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()
    
    return {"status": "success"}

@app.post("/like_sync")
async def sync_like(data: dict):
    like = data.get("like")
    if not like:
        return {"status": "error", "message": "Dados inválidos"}
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO likes (id, post_id, user_id, created_at) VALUES (?, ?, ?, ?)",
            (like["id"], like["post_id"], like["user_id"], like["created_at"])
        )
        conn.commit()
    except:
        pass  # Ignorar erros
    finally:
        conn.close()
    
    return {"status": "success"}

@app.post("/unlike_sync")
async def sync_unlike(data: dict):
    post_id = data.get("post_id")
    user_id = data.get("user_id")
    
    if not post_id or not user_id:
        return {"status": "error", "message": "Dados inválidos"}
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "DELETE FROM likes WHERE post_id = ? AND user_id = ?",
            (post_id, user_id)
        )
        conn.commit()
    except:
        pass  # Ignorar erros
    finally:
        conn.close()
    
    return {"status": "success"}

@app.post("/comment_sync")
async def sync_comment(data: dict):
    comment = data.get("comment")
    if not comment:
        return {"status": "error", "message": "Dados inválidos"}
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO comments (id, post_id, user_id, username, text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (comment["id"], comment["post_id"], comment["user_id"], comment["username"], comment["text"], comment["created_at"])
        )
        conn.commit()
    except:
        pass  # Ignorar erros
    finally:
        conn.close()
    
    return {"status": "success"}

@app.get("/heartbeat")
async def heartbeat():
    return {"status": "online"}

@app.get("/status")
async def server_status():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Contar servidores ativos (não vistos por mais de 5 minutos)
    cursor.execute("SELECT COUNT(*) FROM servers WHERE last_seen > datetime('now', '-5 minutes')")
    active_servers = cursor.fetchone()[0]
    
    # Incluir o próprio servidor
    servers_count = active_servers + 1
    
    conn.close()
    
    return {
        "status": "online",
        "servers_count": servers_count
    }

@app.get("/uploads/{file_path:path}")
async def get_upload(file_path: str):
    return FileResponse(os.path.join(UPLOADS_DIR, file_path))

# Função para inicializar o servidor
def setup_server(server_url=None):
    if server_url:
        os.environ["MY_SERVER_URL"] = server_url
    
    # Registrar em outros servidores conhecidos
    if server_url and server_url != "http://localhost:8000":
        try:
            # Registrar no servidor principal primeiro
            requests.post("http://localhost:8000/register_server", 
                        json={"server_url": server_url}, timeout=5)
        except:
            pass  # Ignorar falhas

# Executar se o script for rodado diretamente
if __name__ == "__main__":
    import uvicorn
    
    import sys
    port = 8000
    server_url = "http://localhost:8000"
    
    # Verificar argumentos de linha de comando
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
        server_url = f"http://localhost:{port}"
    
    setup_server(server_url)
    
    uvicorn.run(app, host="0.0.0.0", port=port)