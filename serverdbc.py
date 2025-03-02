from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import cantools
import can
import threading
import time
import os

app = FastAPI()

# Enable CORS for frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load multiple DBC files
DBC_FILE_PATHS = [
    r"C:\Users\MaxKulick\Downloads\INV_CAN_cm.dbc",
    r"C:\Users\MaxKulick\Downloads\vcu_bms_prop_can.dbc"
]

# Initialize an empty database
dbc = cantools.database.Database()

# Load all DBC files into the database
for dbc_path in DBC_FILE_PATHS:
    try:
        dbc.add_dbc_file(dbc_path)
        print(f"✅ Loaded DBC file: {dbc_path}")
    except Exception as e:
        print(f"❌ Error loading DBC file {dbc_path}: {e}")

# Get all valid CAN IDs from the merged database
valid_can_ids = {msg.frame_id: msg.name for msg in dbc.messages}
print(f"✅ Available CAN Messages: {valid_can_ids}")

# Global vehicle data storage
vehicle_data = {}
# Flag to control the CAN receiver thread
run_can_receiver = True

# Function to receive and decode real CAN data from PEAK CAN
def receive_can_data():
    global vehicle_data
    
    try:
        # Initialize the CAN bus with PEAK CAN interface
        # For PCAN use 'pcan' as the interface
        # You may need to specify a specific channel like 'PCAN_USBBUS1'
        bus = can.interface.Bus(bustype='pcan', channel='PCAN_USBBUS1', bitrate=500000)
        print("✅ Connected to PEAK CAN interface")
        
        # Continuous reception loop
        while run_can_receiver:
            try:
                # Receive a CAN message with 0.1s timeout
                message = bus.recv(0.1)
                
                if message:
                    # Check if the received message ID is in our DBC database
                    if message.arbitration_id in valid_can_ids:
                        # Get the message name from our mapping
                        message_name = valid_can_ids[message.arbitration_id]
                        
                        try:
                            # Decode the message
                            dbc_message = dbc.get_message_by_frame_id(message.arbitration_id)
                            decoded_data = dbc.decode_message(message.arbitration_id, message.data)
                            
                            # Format data with message name prefix for each signal
                            formatted_data = {f"{message_name}.{sig_name}": value 
                                            for sig_name, value in decoded_data.items()}
                            
                            # Update vehicle data dictionary
                            vehicle_data.update(formatted_data)
                            
                            # Log periodically (every 50 messages to avoid console spam)
                            if message.arbitration_id % 50 == 0:
                                print(f"📡 Received CAN ID: 0x{message.arbitration_id:X}, Message: {message_name}")
                        
                        except Exception as decode_error:
                            print(f"⚠️ Error decoding message {message_name} (ID: 0x{message.arbitration_id:X}): {decode_error}")
            
            except can.CanError as e:
                print(f"⚠️ CAN Bus error: {e}")
                time.sleep(1)  # Wait a bit before retrying
                
    except Exception as setup_error:
        print(f"❌ Failed to setup CAN interface: {setup_error}")
        print("⚠️ Falling back to mock data generation")
        # Fall back to mock data if CAN interface fails
        generate_mock_can_data()
    
    finally:
        # Cleanup when thread exits
        if 'bus' in locals():
            bus.shutdown()
            print("💤 CAN bus shutdown")

# Backup function for mock data (in case CAN hardware fails)
def generate_mock_can_data():
    import random
    
    print("🔄 Using mock data generation")
    
    while run_can_receiver:
        # Update all signals every 0.5 seconds
        time.sleep(0.5)
        
        # Generate new values for all messages and signals
        for can_id, message_name in valid_can_ids.items():
            message = dbc.get_message_by_frame_id(can_id)
            
            # Generate random values for each signal
            data_values = {f"{message_name}.{sig.name}": random.uniform(0, 100) for sig in message.signals}
            
            # Store the decoded data
            vehicle_data.update(data_values)
        
        # Print just a confirmation that all signals were updated
        print(f"📡 Updated all mock CAN signals at {time.strftime('%H:%M:%S')}")

# Start the CAN receiver in a separate thread
can_thread = threading.Thread(target=receive_can_data, daemon=True)
can_thread.start()

@app.get("/vehicle_data")
async def get_vehicle_data():
    return vehicle_data

@app.post("/upload_dbc/")
async def upload_dbc(file: UploadFile = File(...)):
    global dbc, valid_can_ids
    new_dbc_path = f"./uploaded_{file.filename}"

    # Save the new DBC file
    with open(new_dbc_path, "wb") as f:
        f.write(await file.read())

    # Reload the DBC
    try:
        # Create a new database
        new_dbc = cantools.database.Database()
        
        # Add all existing files
        for dbc_path in DBC_FILE_PATHS:
            new_dbc.add_dbc_file(dbc_path)
        
        # Add the newly uploaded file
        new_dbc.add_dbc_file(new_dbc_path)
        
        # Replace the old database
        dbc = new_dbc
        valid_can_ids = {msg.frame_id: msg.name for msg in dbc.messages}
        print(f"✅ New DBC Loaded: {file.filename}")
        return {"message": f"Successfully loaded {file.filename}", "available_messages": valid_can_ids}
    except Exception as e:
        return {"error": f"Failed to load DBC file: {str(e)}"}

# Endpoint to get list of available CAN messages
@app.get("/available_messages")
async def get_available_messages():
    return {"messages": valid_can_ids}

# Endpoint to get statistics about received CAN data
@app.get("/statistics")
async def get_statistics():
    return {
        "total_messages": len(valid_can_ids),
        "signals_received": len(vehicle_data),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

# Gracefully handle shutdown
@app.on_event("shutdown")
def shutdown_event():
    global run_can_receiver
    print("🛑 Shutting down CAN receiver")
    run_can_receiver = False
    # Give the thread time to clean up
    time.sleep(0.5)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)