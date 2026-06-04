# IoT Health Monitoring Dashboard - iot.py

Dashboard real-time monitoring tanda-tanda vital pasien berbasis Flask dengan WebSocket.

## ✨ Fitur

- ✅ **Real-time Monitoring** - Grafik real-time untuk ECG, BPM, SpO2
- ✅ **Alert System** - Warning otomatis ketika nilai abnormal
- ✅ **Statistics** - Rata-rata, Min, Max untuk 5 menit terakhir
- ✅ **Dark Mode** - Toggle antara light mode dan dark mode
- ✅ **Historical Data** - Menyimpan data 5 menit terakhir
- ✅ **Responsive Design** - Kompatibel mobile, tablet, desktop
- ✅ **Clean UI** - Interface minimalis dan modern

## 🚀 Cara Menjalankan

### 1. Instalasi Dependencies

```bash
pip install -r requirements.txt
```

### 2. Jalankan Server

```bash
python iot.py
```

Output akan tampil:
```
==================================================
IoT Health Monitoring Dashboard
==================================================
🚀 Server berjalan di http://localhost:5000
📡 Endpoint data: POST /api/data
📊 Dashboard: http://localhost:5000
==================================================
```

### 3. Buka Dashboard

- Browser: **http://localhost:5000**
- atau: **http://127.0.0.1:5000**
- atau: **http://<IP-KOMPUTER>:5000** (dari device lain)

## 📡 Cara Mengintegrasikan dengan ESP32

### Konfigurasi di iot.ino

```cpp
// Flask Server - ganti IP sesuai komputer Anda
const char* serverURL = "http://192.168.1.100:5000/api/data";
```

**Catatan:** Ganti `192.168.1.100` dengan IP komputer Anda!

Untuk mengetahui IP komputer:

**Windows (Command Prompt):**
```bash
ipconfig
# Cari "IPv4 Address"
```

**Linux/macOS (Terminal):**
```bash
ifconfig
# atau
ip addr
```

### Format Data dari ESP32

ESP32 harus mengirim JSON ke endpoint `POST /api/data`:

```json
{
  "bpm": 85,
  "spo2": 98,
  "ecg": -12.45
}
```

## 📊 Interpretasi Status

### Status Pasien

| Status    | Kondisi                      |
|-----------|------------------------------|
| 🟢 AMAN   | BPM normal & SpO2 normal     |
| 🟠 PERHATIAN | BPM atau SpO2 abnormal    |

### Status BPM

| Status   | Range        |
|----------|--------------|
| RENDAH   | < 60 bpm     |
| NORMAL   | 60–100 bpm   |
| TINGGI   | > 100 bpm    |

### Status SpO2

| Status   | Range        |
|----------|--------------|
| NORMAL   | ≥ 95%        |
| RENDAH   | < 95%        |

## 🎨 Fitur Dark Mode

Klik tombol di pojok kanan atas untuk toggle antara light mode dan dark mode.
Preferensi disimpan secara otomatis di browser.

## 📈 Charts dan Statistics

### Real-time Monitoring

- **Heart Rate Chart** - Grafik BPM dengan skalogram waktu
- **SpO2 Chart** - Grafik saturasi oksigen
- **ECG Chart** - Grafik sinyal jantung

### Statistics Panel

Menampilkan untuk **5 menit terakhir**:
- BPM: Average, Min, Max
- SpO2: Average, Min, Max

## 🔄 Reset Data

Tombol "🔄 Reset Data" di bawah akan menghapus semua history dan statistik.

## ⚠️ Alert System

Ketika nilai abnormal terdeteksi:
- Alert box akan muncul di atas
- Status badge berubah menjadi "PERHATIAN" (orange)
- Detail masalah ditampilkan

## 📱 API Endpoints

### GET `/`
Membuka dashboard

```bash
curl http://localhost:5000/
```

### POST `/api/data`
Menerima data sensor dari ESP32

```bash
curl -X POST http://localhost:5000/api/data \
  -H "Content-Type: application/json" \
  -d '{"bpm":85,"spo2":98,"ecg":-12.45}'
```

**Response:**
```json
{
  "status": "success"
}
```

### GET `/api/status`
Mengecek status sistem

```bash
curl http://localhost:5000/api/status
```

**Response:**
```json
{
  "status": "online",
  "timestamp": "2024-06-04T10:30:45.123456",
  "data_points": 150
}
```

### POST `/api/reset`
Reset semua data

```bash
curl -X POST http://localhost:5000/api/reset
```

**Response:**
```json
{
  "status": "success"
}
```

## 🌐 WebSocket Events (Real-time)

### Server → Client

- **`initial_data`** - Dikirim ketika client connect, berisi semua data
- **`data_update`** - Dikirim ketika ada data baru dari sensor

### Client → Server

- **`request_data`** - Client request data terbaru
- **`connect`** - Client connect (otomatis)
- **`disconnect`** - Client disconnect (otomatis)

## ⚙️ Struktur File

```
Dasboard Monitoring/
├── iot.py                    # Server Flask utama
├── requirements.txt          # Python dependencies
├── iot.ino                   # Code ESP32
├── app.py                    # Dashboard alternatif (serial-based)
├── apl.py                    # Aplikasi lainnya
└── templates/
    └── iot_dashboard.html    # Frontend dashboard
```

## 🐛 Troubleshooting

### Port 5000 sudah digunakan

```bash
# Ubah port di iot.py (baris akhir)
socketio.run(app, host='0.0.0.0', port=8000, debug=True)
```

### Data tidak masuk

1. Pastikan IP komputer sudah benar di iot.ino
2. Pastikan WiFi ESP32 dan komputer sama
3. Buka Serial Monitor ESP32 untuk debug
4. Test endpoint manual: 
```bash
curl -X POST http://localhost:5000/api/data \
  -H "Content-Type: application/json" \
  -d '{"bpm":80,"spo2":97,"ecg":-10.5}'
```

### Dashboard tidak connect

1. Refresh browser (Ctrl+F5)
2. Buka console browser (F12) untuk lihat error
3. Pastikan server masih running
4. Check firewall settings

### WebSocket error

Pastikan `flask-socketio` sudah installed:
```bash
pip install flask-socketio python-socketio python-engineio
```

## 📝 Catatan Penting

- Dashboard menyimpan data hanya dalam memory (tidak ada database)
- Data akan hilang jika server restart
- Max history = 300 data points = 5 menit (dengan interval 1 detik)
- Sistem adalah prototipe monitoring, bukan alat medis final

## 🔗 Referensi

- [Flask Documentation](https://flask.palletsprojects.com/)
- [Flask-SocketIO](https://flask-socketio.readthedocs.io/)
- [Chart.js](https://www.chartjs.org/)
- [Bootstrap 5](https://getbootstrap.com/)

---

**Dibuat untuk:** PSB I - Semester 6
**Status:** ✅ Production Ready
