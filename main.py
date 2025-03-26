from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, status
from google.cloud import firestore, secretmanager
from google.cloud.firestore_v1 import Transaction
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from datetime import datetime, timedelta, timezone
import smtplib
from pydantic import BaseModel
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os

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
def get_secret(secret_id: str) -> str:
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/jamble-test-454718/secrets/{secret_id}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")

SECRET_KEY = get_secret("JWT_SECRET_KEY")
SMTP_PASSWORD = get_secret("SMTP_PASSWORD")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "bogdanstefan.agrici@gmail.com")
MAX_TRANSACTION_RETRIES = int(os.getenv("MAX_TRANSACTION_RETRIES", 3))

def send_confirmation_email(email: str, product_name: str):
    #this function won't actually send an email, but it will print the email address and product name
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USERNAME
        msg['To'] = email
        msg['Subject'] = f"New order for product {product_name}"
        
        body = f"Thank you for your order of {product_name}!"
        msg.attach(MIMEText(body, 'plain'))
        
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
            
    except Exception as e:
        print(f"Failed to send email: {str(e)}")



""" API endpoints """

app = FastAPI()
db = firestore.Client(project="jamble-test-454718")

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

@app.post("/place_order")
async def place_order(
    order_data: OrderRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(oauth2_scheme)
):
    buyer_email = order_data.buyer_email
    product_id = order_data.product_id
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("sub") != buyer_email:
            raise HTTPException(status_code=403, detail="Email mismatch")
        
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    @firestore.transactional
    def process_order(transaction):
        product_ref = db.collection("Products").document(product_id)
        product = product_ref.get(transaction=transaction)
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
        
        #create the order in transaction
        orders_ref = db.collection("Orders").document()
        transaction.set(orders_ref, order_data)

        return product_data["product_name"]
    
    #handle transaction errors and retries
    retry_count = 0
    while retry_count < MAX_TRANSACTION_RETRIES:
        try:
            transaction = db.transaction()
            product_name = process_order(transaction)
            background_tasks.add_task(send_confirmation_email, buyer_email, product_name)
            return {"message": f"Order placed for {product_name}"}
            
        except firestore.exceptions.TransactionError:
            retry_count += 1
            if retry_count >= MAX_TRANSACTION_RETRIES:
                raise HTTPException(
                    status_code=503,
                    detail="Transaction failed after maximum retries"
                )
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))