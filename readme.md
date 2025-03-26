# Inventory Management System

## How We Handle Common Issues

### Multiple Simultaneous Orders
We use Firestore transactions to ensure accurate stock management:

```python
def process_order(transaction):
    # Check stock
    product = product_ref.get(transaction=transaction)
    quantity = product_data["quantity"]
    
    # Update if available
    if quantity > 0:
        transaction.update(product_ref, {"quantity": quantity - 1})
        transaction.set(orders_ref, order_data)
    else:
        raise HTTPException(status_code=400, detail="Out of stock")
```

This ensures:
- Orders are processed first-come, first-served
- We can't oversell products
- Updates are atomic
- Concurrent orders are handled safely

### Firestore Transaction Failures
We implement a simple retry mechanism:

```python
# Attempt the transaction up to 3 times
for _ in range(3):
    try:
        process_order(transaction)
        return "Order placed successfully"
    except Exception as e:
        if "out of stock" in str(e):
            return "Product out of stock"
        continue

return "Transaction failed, please try again"
```

This provides:
- Multiple attempts for each transaction
- Clear error messages
- Reasonable retry limits
- Distinction between stock issues and technical failures

### Background Task Considerations
Currently implemented for email notifications:
```python
background_tasks.add_task(send_email, buyer_email, product_name)
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