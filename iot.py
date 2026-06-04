"""
IoT Health Monitoring Dashboard
Real-time monitoring untuk ECG, BPM, SpO2
"""

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from collections import deque
from datetime import datetime, timedelta
import json
import threading
import time

app = Flask(__name__)
app.config['SECRET_KEY'] = 'iot-health-monitor-2024'
socketio = SocketIO(app, cors_allowed_origins="*")

# ==================== KONFIGURASI ====================

# Status rules berdasarkan README
STATUS_RULES = {
    'bpm': {
        'low': {'min': 0, 'max': 59, 'label': 'RENDAH', 'color': '#FF6B6B'},
        'normal': {'min': 60, 'max': 100, 'label': 'NORMAL', 'color': '#51CF66'},
        'high': {'min': 101, 'max': 300, 'label': 'TINGGI', 'color': '#FF6B6B'}
    },
    'spo2': {
        'normal': {'min': 95, 'max': 100, 'label': 'NORMAL', 'color': '#51CF66'},
        'low': {'min': 0, 'max': 94, 'label': 'RENDAH', 'color': '#FF6B6B'}
    }
}

# ==================== DATA STORAGE ====================

class SensorData:
    def __init__(self, max_history=300):  # 5 menit dengan interval 1 detik
        self.max_history = max_history
        self.bpm_history = deque(maxlen=max_history)
        self.spo2_history = deque(maxlen=max_history)
        self.ecg_history = deque(maxlen=max_history)
        self.timestamp_history = deque(maxlen=max_history)
        
        # Current values
        self.current_bpm = 0
        self.current_spo2 = 0
        self.current_ecg = 0
        
        # Statistics
        self.stats = {
            'bpm_avg': 0,
            'bpm_min': 0,
            'bpm_max': 0,
            'spo2_avg': 0,
            'spo2_min': 0,
            'spo2_max': 0,
        }
        
        # Patient status
        self.patient_status = 'AMAN'
        self.status_color = '#51CF66'

sensor_data = SensorData()

# ==================== FUNGSI HELPER ====================

def get_status_bpm(bpm):
    """Tentukan status BPM"""
    if bpm < 60:
        return STATUS_RULES['bpm']['low']
    elif bpm <= 100:
        return STATUS_RULES['bpm']['normal']
    else:
        return STATUS_RULES['bpm']['high']

def get_status_spo2(spo2):
    """Tentukan status SpO2"""
    if spo2 >= 95:
        return STATUS_RULES['spo2']['normal']
    else:
        return STATUS_RULES['spo2']['low']

def get_patient_status(bpm_status, spo2_status):
    """Tentukan status pasien keseluruhan"""
    bpm_normal = bpm_status['label'] == 'NORMAL'
    spo2_normal = spo2_status['label'] == 'NORMAL'
    
    if bpm_normal and spo2_normal:
        return {'status': 'AMAN', 'color': '#51CF66'}
    else:
        return {'status': 'PERHATIAN', 'color': '#FFA500'}

def calculate_statistics():
    """Hitung statistik dari data yang ada"""
    if len(sensor_data.bpm_history) == 0:
        return
    
    bpm_list = list(sensor_data.bpm_history)
    spo2_list = list(sensor_data.spo2_history)
    
    sensor_data.stats = {
        'bpm_avg': round(sum(bpm_list) / len(bpm_list), 1) if bpm_list else 0,
        'bpm_min': min(bpm_list) if bpm_list else 0,
        'bpm_max': max(bpm_list) if bpm_list else 0,
        'spo2_avg': round(sum(spo2_list) / len(spo2_list), 1) if spo2_list else 0,
        'spo2_min': min(spo2_list) if spo2_list else 0,
        'spo2_max': max(spo2_list) if spo2_list else 0,
    }

def get_dashboard_data():
    """Ambil semua data untuk dashboard"""
    return {
        'current': {
            'bpm': sensor_data.current_bpm,
            'spo2': sensor_data.current_spo2,
            'ecg': sensor_data.current_ecg,
        },
        'history': {
            'bpm': list(sensor_data.bpm_history),
            'spo2': list(sensor_data.spo2_history),
            'ecg': list(sensor_data.ecg_history),
            'timestamps': list(sensor_data.timestamp_history),
        },
        'statistics': sensor_data.stats,
        'patient_status': {
            'status': sensor_data.patient_status,
            'color': sensor_data.status_color
        }
    }

# ==================== API ENDPOINTS ====================

@app.route('/')
def index():
    """Halaman utama dashboard"""
    return render_template('iot_dashboard.html')

@app.route('/api/data', methods=['POST'])
def receive_data():
    """Endpoint untuk menerima data dari ESP32"""
    try:
        data = request.get_json()
        
        # Parse data
        bpm = int(data.get('bpm', 0))
        spo2 = int(data.get('spo2', 0))
        ecg = float(data.get('ecg', 0))
        
        # Update current values
        sensor_data.current_bpm = bpm
        sensor_data.current_spo2 = spo2
        sensor_data.current_ecg = ecg
        
        # Add to history
        timestamp = datetime.now().strftime('%H:%M:%S')
        sensor_data.bpm_history.append(bpm)
        sensor_data.spo2_history.append(spo2)
        sensor_data.ecg_history.append(ecg)
        sensor_data.timestamp_history.append(timestamp)
        
        # Calculate status
        bpm_status = get_status_bpm(bpm)
        spo2_status = get_status_spo2(spo2)
        patient_status = get_patient_status(bpm_status, spo2_status)
        
        sensor_data.patient_status = patient_status['status']
        sensor_data.status_color = patient_status['color']
        
        # Calculate statistics
        calculate_statistics()
        
        # Broadcast to all connected clients
        socketio.emit('data_update', get_dashboard_data(), broadcast=True)
        
        # Log
        print(f"[{timestamp}] BPM: {bpm} | SpO2: {spo2}% | ECG: {ecg} | Status: {sensor_data.patient_status}")
        
        return jsonify({'status': 'success'}), 200
    
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 400

@app.route('/api/status', methods=['GET'])
def get_status():
    """Endpoint untuk mengecek status sistem"""
    return jsonify({
        'status': 'online',
        'timestamp': datetime.now().isoformat(),
        'data_points': len(sensor_data.bpm_history)
    }), 200

@app.route('/api/reset', methods=['POST'])
def reset_data():
    """Reset semua data"""
    sensor_data.bpm_history.clear()
    sensor_data.spo2_history.clear()
    sensor_data.ecg_history.clear()
    sensor_data.timestamp_history.clear()
    sensor_data.current_bpm = 0
    sensor_data.current_spo2 = 0
    sensor_data.current_ecg = 0
    sensor_data.patient_status = 'AMAN'
    sensor_data.status_color = '#51CF66'
    
    socketio.emit('data_update', get_dashboard_data(), broadcast=True)
    return jsonify({'status': 'success'}), 200

# ==================== WEBSOCKET EVENTS ====================

@socketio.on('connect')
def handle_connect():
    """Client connect - kirim data current"""
    print(f"Client connected")
    emit('initial_data', get_dashboard_data())

@socketio.on('disconnect')
def handle_disconnect():
    """Client disconnect"""
    print(f"Client disconnected")

@socketio.on('request_data')
def handle_request_data():
    """Client request data"""
    emit('data_update', get_dashboard_data())

# ==================== MAIN ====================

if __name__ == '__main__':
    print("=" * 50)
    print("IoT Health Monitoring Dashboard")
    print("=" * 50)
    print("🚀 Server berjalan di http://localhost:5000")
    print("📡 Endpoint data: POST /api/data")
    print("📊 Dashboard: http://localhost:5000")
    print("=" * 50)
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
