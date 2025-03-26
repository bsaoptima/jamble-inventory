# Inventory Management System

## How We Handle Common Issues

### Multiple Simultaneous Orders
We use Firestore transactions to ensure accurate stock management:

```python
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
```

This ensures:
- Orders are processed first-come, first-served
- We can't oversell products
- Updates are atomic
- Concurrent orders are handled safely

### Firestore Transaction Failures
We implement a retry mechanism:

```python
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
```

This provides:
- Multiple attempts for each transaction
- Clear error messages
- Reasonable retry limits
- Distinction between stock issues and technical failures

### Background Task Considerations
Currently implemented for email notifications:
```python
def send_confirmation_email(email: str, product_name: str):
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
```

Potential issues:
1. **Task Persistence**
   - Tasks don't survive server restarts
   - No built-in retry system
   - Failed tasks are hard to track

2. **Resource Management**
   - Tasks share server resources
   - Can affect API performance
   - No task limiting

3. **Error Handling**
   - Difficult to report failures
   - Limited monitoring options
   - No customer feedback for failed tasks

Suggested improvements:
- Implement proper message queue
- Add task status tracking
- Set up monitoring
- Consider separate service for heavy tasks

## Testing
The `simulation.ipynb` notebook tests concurrent orders by creating multiple users and running simultaneous requests to verify stock management and error handling.