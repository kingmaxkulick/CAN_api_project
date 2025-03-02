from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import threading
import time
import random

app = FastAPI()

# Enable CORS for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Change this later for production security
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simulated vehicle data storage
vehicle_data = {
    "charge_percent": 80,
    "charging_rate": 7.2,  # kW
    "full_charge_time": 30,  # Minutes
    "battery_temp": 35.2,
    "motor_temp": 75.5,
    "inverter_temp": 65.3,
    "brake_temp": 50.1,
    "tire_temp": [34.5, 35.2, 33.9, 34.7],  # Per tire
    "tire_pressure": [32, 32, 31, 32],  # PSI per tire
    "power_output": 250,  # kW
    "torque_distribution": [50, 50],  # Front/Rear
    "suspension_metrics": [2.1, 2.2, 2.0, 2.3],  # Shock compression levels
    "g_forces": [0.3, 0.4, 0.2],  # X, Y, Z axis
    "vehicle_state": {
        "primary_state": "CHARGE",
        "sub_state": "FAST_CHARGE",
        "status_flags": ["HV_ENABLED", "DC_FAST"],
        "fault_present": False,
        "message_counter": 42
    },
    "fault_status": {
        "source": None,
        "type": None,
        "severity": 0,
        "timestamp": int(time.time()),
        "counter": 0,
        "active": False
    }
}

# Mock data generator (updates every .5 seconds)
def update_mock_vehicle_data():
    while True:
        time.sleep(.5)
        vehicle_data["charge_percent"] = max(0, vehicle_data["charge_percent"] - 1)  # Simulate slow discharge
        vehicle_data["battery_temp"] += random.uniform(-0.5, 0.5)  # Small fluctuation
        vehicle_data["motor_temp"] += random.uniform(-1.0, 1.0)  # Motor temperature variation
        vehicle_data["tire_temp"] = [temp + random.uniform(-0.5, 0.5) for temp in vehicle_data["tire_temp"]]
        vehicle_data["tire_pressure"] = [pressure + random.uniform(-0.2, 0.2) for pressure in vehicle_data["tire_pressure"]]
        print(f"Updated vehicle data: {vehicle_data['charge_percent']}% charge")

threading.Thread(target=update_mock_vehicle_data, daemon=True).start()

@app.get("/vehicle_data")
async def get_vehicle_data():
    return vehicle_data

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
