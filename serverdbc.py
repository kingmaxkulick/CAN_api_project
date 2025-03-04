from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import uvicorn
import cantools
import can
import threading
import time
import os
import csv
from datetime import datetime
from typing import Dict, List, Any

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

# Logging-related global variables
is_logging = False
log_data = []
log_start_time = None
current_log_id = 0
log_directory = "./logs"

# Ensure log directory exists
os.makedirs(log_directory, exist_ok=True)

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
    global vehicle_data, is_logging, log_data, log_start_time
    
    # If logging is active, add the current data to the log
    if is_logging:
        timestamp = datetime.now()
        
        # Only log if at least 0.5 seconds has passed since last log
        if not hasattr(app.state, 'last_log_time') or (timestamp - app.state.last_log_time).total_seconds() >= 0.5:
            elapsed_ms = int((timestamp - log_start_time).total_seconds() * 1000) if log_start_time else 0
            
            # Create a log entry with timestamp
            log_entry = {
                "timestamp": timestamp.isoformat(),
                "elapsed_ms": elapsed_ms
            }
            
            # Check if we're logging specific signals or all data
            signals_to_log = getattr(app.state, 'signals_to_log', None)
            
            if signals_to_log:
                # Only log the specified signals
                filtered_data = {key: vehicle_data[key] for key in signals_to_log if key in vehicle_data}
                log_entry.update(filtered_data)
            else:
                # Log all vehicle data
                log_entry.update(vehicle_data)
                
            log_data.append(log_entry)
            app.state.last_log_time = timestamp
    
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

@app.get("/logging/debug")
async def debug_logging():
    signals_to_log = getattr(app.state, 'signals_to_log', None)
    sample_vehicle_data = {}
    
    # Get a small sample of vehicle data keys
    if vehicle_data:
        sample_keys = list(vehicle_data.keys())[:5]
        sample_vehicle_data = {k: vehicle_data[k] for k in sample_keys}
    
    return {
        "is_logging": is_logging,
        "signals_to_log": signals_to_log,
        "signals_to_log_count": len(signals_to_log) if signals_to_log else 0,
        "app_state_keys": [key for key in dir(app.state) if not key.startswith('_')],
        "sample_vehicle_data_keys": list(vehicle_data.keys())[:10] if vehicle_data else [],
        "vehicle_data_count": len(vehicle_data) if vehicle_data else 0,
        "sample_vehicle_data": sample_vehicle_data
    }

# ===== LOGGING API ENDPOINTS =====

@app.get("/logging/status")
async def get_logging_status():
    global is_logging, log_data, current_log_id
    return {
        "is_logging": is_logging,
        "entries_count": len(log_data),
        "current_log_id": current_log_id
    }

# Define a Pydantic model for the request
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class LoggingRequest(BaseModel):
    signals_to_log: Optional[List[str]] = None

@app.post("/logging/start")
async def start_logging(request: LoggingRequest):
    global is_logging, log_data, log_start_time, current_log_id
    
    if is_logging:
        return {"status": "already_logging", "message": "Logging is already in progress"}
    
    # Clear previous log data and start fresh
    log_data = []
    log_start_time = datetime.now()
    is_logging = True
    
    # Store the list of signals to log if provided
    signals_to_log = request.signals_to_log
    app.state.signals_to_log = signals_to_log
    
    # Find the next log ID
    current_log_id = 1
    while os.path.exists(os.path.join(log_directory, f"keymetrics-{current_log_id}.csv")):
        current_log_id += 1
    
    print(f"✅ Started logging with ID {current_log_id}")
    if signals_to_log:
        print(f"  📊 Logging {len(signals_to_log)} specific signals")
        for sig in signals_to_log[:5]:  # Print first 5 for debugging
            print(f"      - {sig}")
        if len(signals_to_log) > 5:
            print(f"      - ... and {len(signals_to_log) - 5} more")
    else:
        print(f"  📊 Logging all signals")
        
    return {
        "status": "started", 
        "message": f"Logging started with ID {current_log_id}",
        "log_id": current_log_id,
        "signals_count": len(signals_to_log) if signals_to_log else "all"
    }

@app.post("/logging/stop")
async def stop_logging(background_tasks: BackgroundTasks):
    global is_logging, log_data, current_log_id
    
    if not is_logging:
        return {"status": "not_logging", "message": "Logging is not in progress"}
    
    if len(log_data) == 0:
        is_logging = False
        return {"status": "empty", "message": "No data logged"}
    
    # Generate the filename
    filename = f"keymetrics-{current_log_id}.csv"
    filepath = os.path.join(log_directory, filename)
    
    # Schedule background task to save the file
    background_tasks.add_task(save_log_to_csv, log_data, filepath)
    
    # Schedule cleanup of old log files (keep only the 5 most recent)
    background_tasks.add_task(cleanup_old_logs, 5)
    
    # Update status
    is_logging = False
    
    print(f"✅ Stopped logging. Saving to {filename} ({len(log_data)} entries)")
    print(f"🧹 Cleaning up old log files (keeping most recent 5)")
    return {
        "status": "stopped", 
        "message": f"Logging stopped. Saving to {filename}",
        "log_id": current_log_id,
        "entry_count": len(log_data),
        "filename": filename
    }

@app.get("/logging/download/{log_id}")
async def download_log(log_id: int):
    filepath = os.path.join(log_directory, f"keymetrics-{log_id}.csv")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail=f"Log file keymetrics-{log_id}.csv not found")
    
    return FileResponse(
        filepath, 
        media_type="text/csv", 
        filename=f"keymetrics-{log_id}.csv"
    )

@app.get("/logging/list")
async def list_logs():
    log_files = []
    for filename in os.listdir(log_directory):
        if filename.startswith("keymetrics-") and filename.endswith(".csv"):
            filepath = os.path.join(log_directory, filename)
            log_files.append({
                "filename": filename,
                "size_bytes": os.path.getsize(filepath),
                "created": datetime.fromtimestamp(os.path.getctime(filepath)).isoformat(),
                "id": int(filename.split("-")[1].split(".")[0])
            })
    
    return sorted(log_files, key=lambda x: x["id"])

# Helper function to save log data to CSV
def save_log_to_csv(data: List[Dict[str, Any]], filepath: str):
    if not data:
        return False
    
    try:
        # Get all possible field names from the data
        fieldnames = set()
        for entry in data:
            fieldnames.update(entry.keys())
        
        # Sort fieldnames for consistent column order - timestamp and elapsed_ms first
        sorted_fieldnames = ["timestamp", "elapsed_ms"]
        sorted_fieldnames.extend(sorted([f for f in fieldnames if f not in sorted_fieldnames]))
        
        # Write to CSV file
        with open(filepath, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=sorted_fieldnames)
            writer.writeheader()
            writer.writerows(data)
        
        print(f"✅ Log file saved successfully: {filepath}")
        return True
    except Exception as e:
        print(f"❌ Error saving log file: {e}")
        return False

# Helper function to clean up old log files
def cleanup_old_logs(max_files_to_keep=5):
    try:
        # Get all log files
        log_files = []
        for filename in os.listdir(log_directory):
            if filename.startswith("keymetrics-") and filename.endswith(".csv"):
                filepath = os.path.join(log_directory, filename)
                log_files.append({
                    "filename": filename,
                    "filepath": filepath,
                    "created": os.path.getctime(filepath),
                    "id": int(filename.split("-")[1].split(".")[0])
                })
        
        # Sort by creation time (newest first)
        log_files.sort(key=lambda x: x["created"], reverse=True)
        
        # If we have more files than the limit, delete the oldest ones
        if len(log_files) > max_files_to_keep:
            files_to_delete = log_files[max_files_to_keep:]
            for file_info in files_to_delete:
                try:
                    os.remove(file_info["filepath"])
                    print(f"🗑️ Deleted old log file: {file_info['filename']}")
                except Exception as e:
                    print(f"⚠️ Error deleting {file_info['filename']}: {e}")
            
            return len(files_to_delete)
        return 0
    except Exception as e:
        print(f"⚠️ Error during log cleanup: {e}")
        return 0

# Gracefully handle shutdown
@app.on_event("shutdown")
def shutdown_event():
    global run_can_receiver, is_logging
    print("🛑 Shutting down CAN receiver")
    
    # Stop logging if active
    if is_logging:
        print("⚠️ Logging was active during shutdown, attempting to save data...")
        is_logging = False
        
        if log_data and len(log_data) > 0:
            filename = f"keymetrics-{current_log_id}.csv"
            filepath = os.path.join(log_directory, filename)
            save_log_to_csv(log_data, filepath)
    
    run_can_receiver = False
    # Give the thread time to clean up
    time.sleep(0.5)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)