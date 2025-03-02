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
    r"C:\Users\MaxKulick\Downloads\NX0002-STS01_A01 (2).dbc"
    
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

# List of message IDs to ignore (add problematic ones here)
IGNORED_MESSAGE_IDS = [0x467]  # BMS_TX_STATE_8 (ID: 0x467)

print(f"⚠️ Ignoring messages: {', '.join([f'0x{id:X}' for id in IGNORED_MESSAGE_IDS])}")

# Global vehicle data storage
vehicle_data = {}
# Flag to control the CAN receiver thread
run_can_receiver = True
# Statistics for monitoring
statistics = {
    "messages_received": 0,
    "messages_decoded": 0,
    "messages_ignored": 0,
    "errors": {},
    "start_time": time.time()
}

# Function to receive and decode real CAN data
def receive_can_data():
    global vehicle_data, statistics
    
    try:
        # Initialize the CAN bus with PEAK CAN interface
        bus = can.interface.Bus(interface='pcan', channel='PCAN_USBBUS1', bitrate=500000)
        print("✅ Connected to PEAK CAN interface")
        
        # Continuous reception loop
        while run_can_receiver:
            try:
                # Receive a CAN message with 0.1s timeout
                message = bus.recv(0.1)
                
                if message:
                    statistics["messages_received"] += 1
                    
                    # Skip messages in the ignore list
                    if message.arbitration_id in IGNORED_MESSAGE_IDS:
                        statistics["messages_ignored"] += 1
                        continue
                    
                    # Check if the received message ID is in our DBC database
                    if message.arbitration_id in valid_can_ids:
                        # Get the message name from our mapping
                        message_name = valid_can_ids[message.arbitration_id]
                        
                        try:
                            # Decode the message
                            decoded_data = dbc.decode_message(message.arbitration_id, message.data)
                            
                            # Format data with message name prefix for each signal
                            formatted_data = {f"{message_name}.{sig_name}": value 
                                            for sig_name, value in decoded_data.items()}
                            
                            # Update vehicle data dictionary
                            vehicle_data.update(formatted_data)
                            statistics["messages_decoded"] += 1
                            
                            # Log periodically (only every 100th message to reduce console output)
                            if statistics["messages_decoded"] % 100 == 0:
                                print(f"📡 Processed {statistics['messages_decoded']} messages, ignored {statistics['messages_ignored']}")
                        
                        except Exception as decode_error:
                            # Track error by message ID
                            error_key = f"0x{message.arbitration_id:X}"
                            if error_key not in statistics["errors"]:
                                statistics["errors"][error_key] = {
                                    "count": 0,
                                    "last_error": "",
                                    "data_sample": ""
                                }
                            
                            statistics["errors"][error_key]["count"] += 1
                            statistics["errors"][error_key]["last_error"] = str(decode_error)
                            statistics["errors"][error_key]["data_sample"] = message.data.hex()
                            
                            # Only print every 1000th error to prevent log flooding
                            if statistics["errors"][error_key]["count"] % 1000 == 1:
                                print(f"⚠️ Error decoding message {message_name} (ID: 0x{message.arbitration_id:X}): {decode_error}")
            
            except can.CanError as e:
                print(f"⚠️ CAN Bus error: {e}")
                time.sleep(1)  # Wait a bit before retrying
                
    except Exception as setup_error:
        print(f"❌ Failed to setup CAN interface: {setup_error}")
    
    finally:
        # Cleanup when thread exits
        if 'bus' in locals():
            bus.shutdown()
            print("💤 CAN bus shutdown")

# Start the CAN receiver in a separate thread
can_thread = threading.Thread(target=receive_can_data, daemon=True)
can_thread.start()

@app.get("/vehicle_data")
async def get_vehicle_data():
    return vehicle_data

@app.get("/can_statistics")
async def get_statistics():
    global statistics
    
    # Calculate uptime
    uptime = time.time() - statistics["start_time"]
    
    return {
        "messages_received": statistics["messages_received"],
        "messages_decoded": statistics["messages_decoded"],
        "messages_ignored": statistics["messages_ignored"],
        "uptime_seconds": uptime,
        "messages_per_second": statistics["messages_received"] / max(1, uptime),
        "error_counts": {id: data["count"] for id, data in statistics["errors"].items()},
        "ignored_messages": [f"0x{id:X}" for id in IGNORED_MESSAGE_IDS]
    }

@app.get("/can_errors")
async def get_error_details():
    global statistics
    return {"errors": statistics["errors"]}

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

@app.get("/available_messages")
async def get_available_messages():
    return {"messages": valid_can_ids}

@app.post("/ignore_message/{message_id}")
async def ignore_message(message_id: str):
    try:
        # Convert from hex string to int
        if message_id.startswith("0x"):
            msg_id = int(message_id, 16)
        else:
            msg_id = int(message_id)
            
        if msg_id not in IGNORED_MESSAGE_IDS:
            IGNORED_MESSAGE_IDS.append(msg_id)
            return {"message": f"Now ignoring message ID: 0x{msg_id:X}"}
        else:
            return {"message": f"Already ignoring message ID: 0x{msg_id:X}"}
    except ValueError:
        return {"error": "Invalid message ID format"}

@app.on_event("shutdown")
async def shutdown_event():
    global run_can_receiver
    print("🛑 Shutting down CAN receiver")
    run_can_receiver = False
    # Give the thread time to clean up
    time.sleep(0.5)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)