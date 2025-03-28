from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, status
from google.cloud import firestore, secretmanager
from google.cloud.firestore_v1.transaction import Transaction
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from datetime import datetime, timedelta, timezone
import smtplib
from pydantic import BaseModel
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from functools import wraps

#Auth setup & functions
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

class UserCreate(BaseModel):
    email: str
    password: str

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

#mock user db
fake_users_db = {
    "test@example.com": {
        "email": "test@example.com",
        "hashed_password": pwd_context.hash("testpassword")
    }
}

#Secret manager fetch
async def get_secret(secret_id: str) -> str:
    client = secretmanager.SecretManagerServiceAsyncClient() #make it async
    name = f"projects/jamble-test-454718/secrets/{secret_id}/versions/latest"
    response = await client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")



SECRET_KEY = None
SMTP_PASSWORD = None
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "bogdanstefan.agrici@gmail.com")
MAX_TRANSACTION_RETRIES = int(os.getenv("MAX_TRANSACTION_RETRIES", 3))


async def send_confirmation_email(email: str, product_name: str):
    #this function won't actually send an email, but it will print the email address and product name
    print("email sent to ", email, "for product ", product_name)

""" API endpoints """

app = FastAPI()
db = firestore.AsyncClient(project="jamble-test-454718")

@app.on_event("startup")
async def startup():
    global SECRET_KEY, SMTP_PASSWORD
    SECRET_KEY = await get_secret("JWT_SECRET_KEY")
    SMTP_PASSWORD = await get_secret("SMTP_PASSWORD")

@app.get("/")
async def home():
    return {"message": "Hello World"}

@app.post("/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = fake_users_db.get(form_data.username)
    if not user or not pwd_context.verify(form_data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    access_token = create_access_token(data={"sub": user["email"]})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/register")
async def register(user: UserCreate):
    if user.email in fake_users_db:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    fake_users_db[user.email] = {
        "email": user.email,
        "hashed_password": pwd_context.hash(user.password)
    }
    return {"message": "User registered successfully"}

class OrderRequest(BaseModel):
    buyer_email: str
    product_id: str

def verify_token_email(func):
    @wraps(func)
    async def wrapper(order_data: OrderRequest, *args, token: str = Depends(oauth2_scheme), **kwargs):
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            if payload.get("sub") != order_data.buyer_email:
                raise HTTPException(status_code=403, detail="Email mismatch")
            return await func(order_data=order_data, token=token, *args, **kwargs)
        except JWTError:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    return wrapper

@app.post("/place_order")
@verify_token_email
async def place_order(
    order_data: OrderRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(oauth2_scheme)
):
    buyer_email = order_data.buyer_email
    product_id = order_data.product_id
    
    transaction = db.transaction()
    product_ref = db.collection("Products").document(product_id)

    @firestore.async_transactional
    async def update_in_transaction(transaction: Transaction, product_ref):
        product = await product_ref.get(transaction=transaction)
        if not product.exists:
            raise HTTPException(status_code=404, detail="Product not found")
        
        product_data = product.to_dict()
        quantity = int(product_data["quantity"])
        if product_data["status"] != "in_stock" or quantity <= 0:
            raise HTTPException(status_code=400, detail="Product out of stock")

        new_quantity = quantity - 1
        transaction.update(product_ref, {"quantity": new_quantity})

        if new_quantity == 0:
            transaction.update(product_ref, {"status": "out_of_stock"})
        
        order_data = {
            "created_at": firestore.SERVER_TIMESTAMP,
            "buyer_email": buyer_email,
            "product_id": product_id,
        }
        
        orders_ref = db.collection("Orders").document()
        transaction.set(orders_ref, order_data)

        return product_data["product_name"]
    
    retry_count = 0
    while retry_count < MAX_TRANSACTION_RETRIES:
        try:
            product_name = await update_in_transaction(transaction, product_ref)
            break
            
        except Exception as e:
            retry_count += 1
            if retry_count >= MAX_TRANSACTION_RETRIES:
                raise HTTPException(
                    status_code=503,
                    detail=f"Transaction failed after {MAX_TRANSACTION_RETRIES} retries: {str(e)}"
                )
            transaction = db.transaction()
            continue
    
    try:
        background_tasks.add_task(send_confirmation_email, buyer_email, product_name)
    except Exception as e:
        print(f"Failed to queue confirmation email: {str(e)}")
    
    return {"message": f"Order placed for {product_name}"}