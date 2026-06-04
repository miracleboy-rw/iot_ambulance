import os
import json
import time
import math
import random
import threading
from datetime import datetime
from flask import Flask, render_template, render_template_string
from flask_socketio import SocketIO, emit

# ============================================================
# KONFIGURASI — Ganti sesuai port Serial ESP32 kamu
# ============================================================
SERIAL_PORT = "COM3"      # Windows: "COM3", "COM4", dll
BAUD_RATE   = 115200
DEMO_MODE   = True        # True = gunakan data simulasi (tanpa ESP32)
                          # False = baca dari Serial USB nyata
# ============================================================

app = Flask(__name__)
app.config["SECRET_KEY"] = "health_monitor_secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# State global (tidak pakai database)
latest_data = {
    "ecg": 0.0,
    "bpm": None,
    "spo2": None,
    "timestamp": "--:--:--",
    "connected": False,
}


# ─────────────────────────────────────────────
# Status helpers
# ─────────────────────────────────────────────
def bpm_status(bpm):
    if bpm is None:
        return "TUNGGU", "waiting"
    if bpm < 60:
        return "RENDAH", "low"
    if bpm > 100:
        return "TINGGI", "high"
    return "AMAN", "normal"


def spo2_status(spo2):
    if spo2 is None:
        return "TUNGGU", "waiting"
    if spo2 < 95:
        return "RENDAH", "low"
    return "NORMAL", "normal"


def patient_status(bpm, spo2):
    if bpm is None or spo2 is None:
        return "TUNGGU", "waiting"
    if 60 <= bpm <= 100 and spo2 >= 95:
        return "AMAN", "normal"
    return "PERHATIAN", "attention"


# ─────────────────────────────────────────────
# Demo mode — simulasi sinyal ECG + sensor
# ─────────────────────────────────────────────
_demo_t = 0.0
_demo_bpm = 75
_demo_spo2 = 98


def _ecg_simulate(t):
    """Simulasi bentuk gelombang ECG sederhana (PQRST)."""
    cycle = t % 1.0
    val = 0.0
    if 0.05 < cycle < 0.15:
        val = 0.25 * math.sin(math.pi * (cycle - 0.05) / 0.10)
    elif 0.18 < cycle < 0.22:
        val = -0.15 * math.sin(math.pi * (cycle - 0.18) / 0.02)
    elif 0.22 < cycle < 0.30:
        val = 1.20 * math.sin(math.pi * (cycle - 0.22) / 0.08)
    elif 0.30 < cycle < 0.34:
        val = -0.35 * math.sin(math.pi * (cycle - 0.30) / 0.04)
    elif 0.38 < cycle < 0.55:
        val = 0.35 * math.sin(math.pi * (cycle - 0.38) / 0.17)
    val += random.gauss(0, 0.015)
    return round(val, 4)


def demo_reader():
    """Thread simulasi data saat DEMO_MODE=True."""
    global _demo_t, _demo_bpm, _demo_spo2, latest_data
    while True:
        _demo_t += 0.02
        if random.random() < 0.005:
            _demo_bpm = max(58, min(105, _demo_bpm + random.randint(-3, 3)))
        if random.random() < 0.003:
            _demo_spo2 = max(92, min(100, _demo_spo2 + random.randint(-1, 1)))

        ecg_val = _ecg_simulate(_demo_t)
        now = datetime.now().strftime("%H:%M:%S")

        latest_data = {
            "ecg": ecg_val,
            "bpm": _demo_bpm,
            "spo2": _demo_spo2,
            "timestamp": now,
            "connected": True,
        }
        socketio.emit("sensor_data", latest_data)
        time.sleep(0.02)


# ─────────────────────────────────────────────
# Real Serial reader
# ─────────────────────────────────────────────
def serial_reader():
    """Baca data JSON dari ESP32 via Serial USB."""
    global latest_data
    try:
        import serial
    except ImportError:
        print("[ERROR] PySerial belum terinstall. Jalankan: pip install pyserial")
        return

    ser = None
    while True:
        try:
            if ser is None or not ser.is_open:
                print(f"[Serial] Menghubungkan ke {SERIAL_PORT} @ {BAUD_RATE}...")
                ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
                print("[Serial] Terhubung.")
                socketio.emit("device_status", {"connected": True})

            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue

            try:
                data = json.loads(line)
                ecg  = float(data.get("ecg", 0.0))
                bpm  = int(data.get("bpm", 0)) or None
                spo2 = int(data.get("spo2", 0)) or None
                now  = datetime.now().strftime("%H:%M:%S")

                latest_data = {
                    "ecg": ecg,
                    "bpm": bpm,
                    "spo2": spo2,
                    "timestamp": now,
                    "connected": True,
                }
                socketio.emit("sensor_data", latest_data)
            except (json.JSONDecodeError, ValueError, KeyError):
                pass

        except Exception as e:
            print(f"[Serial] Error: {e}")
            latest_data["connected"] = False
            socketio.emit("device_status", {"connected": False})
            if ser:
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
            time.sleep(3)


DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Patient Health Monitoring Dashboard</title>
  <script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&family=Fraunces:wght@700&display=swap" rel="stylesheet"/>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg:        #F0F2F5;
      --surface:   #FFFFFF;
      --border:    #E4E8EE;
      --text-1:    #0D1117;
      --text-2:    #5A6478;
      --text-3:    #8C95A6;
      --accent:    #1A7FDB;
      --normal-bg: #EBF8F2;
      --normal-fg: #0E7A4A;
      --low-bg:    #FFF3E0;
      --low-fg:    #E65100;
      --high-bg:   #FFEBEE;
      --high-fg:   #C62828;
      --wait-bg:   #F5F5F5;
      --wait-fg:   #9E9E9E;
      --ecg-line:  #1A7FDB;
      --shadow:    0 2px 12px rgba(0,0,0,.07);
      --radius:    14px;
    }

    html, body {
      height: 100%;
      background: var(--bg);
      font-family: 'DM Sans', sans-serif;
      color: var(--text-1);
    }

    body {
      display: flex;
      flex-direction: column;
      min-height: 100vh;
      padding: 0 0 32px;
    }


    /* ── Watermark Background Logo ── */
    .main::before {
      content: '';
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      width: 520px;
      height: 520px;
      background-image: url('data:image/png;base64,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');
      background-size: contain;
      background-repeat: no-repeat;
      background-position: center;
      opacity: 0.08;
      pointer-events: none;
      z-index: 0;
    }

    .main {
      position: relative;
    }

    .cards-row, .section-divider {
      position: relative;
      z-index: 1;
    }

    .card {
      background: rgba(255,255,255,0.80) !important;
    }

    .ecg-card {
      background: rgba(255,255,255,0.80) !important;
      position: relative;
      z-index: 1;
    }


    /* ── Header ── */
    .header {
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 18px 32px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 100;
      box-shadow: 0 1px 8px rgba(0,0,0,.05);
    }

    .header-left {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .header-icon {
      width: 38px; height: 38px;
      background: var(--accent);
      border-radius: 10px;
      display: flex; align-items: center; justify-content: center;
    }
    .header-icon svg { width:20px; height:20px; fill:none; stroke:#fff; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; }

    .header-title {
      font-family: 'Fraunces', serif;
      font-size: 1.25rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      color: var(--text-1);
    }
    .header-subtitle {
      font-size: .72rem;
      color: var(--text-3);
      margin-top: 1px;
      font-weight: 400;
    }

    .header-right {
      display: flex;
      align-items: center;
      gap: 16px;
    }

    .refresh-time {
      font-family: 'DM Mono', monospace;
      font-size: .8rem;
      color: var(--text-2);
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .refresh-dot {
      width: 7px; height: 7px;
      border-radius: 50%;
      background: #ccc;
      transition: background .4s;
    }
    .refresh-dot.online { background: #22c55e; box-shadow: 0 0 0 3px rgba(34,197,94,.2); }
    .refresh-dot.offline { background: #ef4444; }

    .device-badge {
      font-size: .72rem;
      padding: 3px 10px;
      border-radius: 20px;
      font-weight: 500;
      background: var(--wait-bg);
      color: var(--wait-fg);
      transition: all .3s;
    }
    .device-badge.online { background: var(--normal-bg); color: var(--normal-fg); }
    .device-badge.offline { background: var(--high-bg); color: var(--high-fg); }

    /* ── Main layout ── */
    .main {
      flex: 1;
      padding: 28px 32px 0;
      max-width: 1400px;
      width: 100%;
      margin: 0 auto;
    }

    /* ── Card grid ── */
    .cards-row {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 18px;
      margin-bottom: 20px;
    }

    .card {
      background: var(--surface);
      border-radius: var(--radius);
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
      padding: 24px 28px;
      display: flex;
      flex-direction: column;
      gap: 4px;
      transition: box-shadow .2s;
    }
    .card:hover { box-shadow: 0 4px 24px rgba(0,0,0,.10); }

    .card-label-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 6px;
    }
    .card-icon {
      width: 36px; height: 36px;
      border-radius: 9px;
      display: flex; align-items: center; justify-content: center;
      font-size: 1rem;
    }
    .card-icon.bpm   { background: #FFF0F3; }
    .card-icon.spo2  { background: #EBF3FF; }
    .card-icon.status{ background: #F0FFF8; }

    .card-category {
      font-size: .72rem;
      font-weight: 600;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: var(--text-3);
    }

    .card-value {
      font-family: 'Fraunces', serif;
      font-size: 3.2rem;
      font-weight: 700;
      line-height: 1;
      letter-spacing: -0.04em;
      color: var(--text-1);
      margin: 4px 0 2px;
    }
    .card-value .unit {
      font-family: 'DM Sans', sans-serif;
      font-size: 1rem;
      font-weight: 400;
      color: var(--text-3);
      margin-left: 4px;
      letter-spacing: 0;
    }
    .card-value.big-status {
      font-size: 2.4rem;
    }

    .card-desc {
      font-size: .82rem;
      color: var(--text-2);
      margin-bottom: 10px;
    }

    .status-chip {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: .75rem;
      font-weight: 600;
      letter-spacing: .05em;
      text-transform: uppercase;
      padding: 4px 10px;
      border-radius: 20px;
      width: fit-content;
    }
    .status-chip::before {
      content: '';
      width: 6px; height: 6px;
      border-radius: 50%;
    }
    .status-chip.normal  { background: var(--normal-bg); color: var(--normal-fg); }
    .status-chip.normal::before  { background: var(--normal-fg); }
    .status-chip.low     { background: var(--low-bg); color: var(--low-fg); }
    .status-chip.low::before     { background: var(--low-fg); }
    .status-chip.high    { background: var(--high-bg); color: var(--high-fg); }
    .status-chip.high::before    { background: var(--high-fg); }
    .status-chip.waiting { background: var(--wait-bg); color: var(--wait-fg); }
    .status-chip.waiting::before { background: var(--wait-fg); }
    .status-chip.attention { background: #FFF3E0; color: #E65100; }
    .status-chip.attention::before { background: #E65100; }

    .patient-status-value {
      font-family: 'Fraunces', serif;
      font-size: 2.6rem;
      font-weight: 700;
      letter-spacing: -.03em;
      margin: 8px 0 4px;
      transition: color .4s;
    }
    .patient-status-value.normal    { color: var(--normal-fg); }
    .patient-status-value.attention { color: #E65100; }
    .patient-status-value.waiting   { color: var(--wait-fg); }

    /* ── ECG Card ── */
    .ecg-card {
      background: var(--surface);
      border-radius: var(--radius);
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
      padding: 24px 28px;
    }

    .ecg-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 16px;
    }
    .ecg-title-group { display: flex; flex-direction: column; gap: 2px; }
    .ecg-title {
      font-family: 'Fraunces', serif;
      font-size: 1rem;
      font-weight: 700;
      letter-spacing: -.01em;
    }
    .ecg-subtitle { font-size: .72rem; color: var(--text-3); }
    .ecg-badge {
      font-size: .7rem;
      font-weight: 600;
      letter-spacing: .06em;
      text-transform: uppercase;
      padding: 3px 10px;
      border-radius: 20px;
      background: #EBF3FF;
      color: var(--accent);
    }

    .ecg-canvas-wrap {
      position: relative;
      height: 200px;
    }
    #ecgChart { width: 100% !important; height: 100% !important; }

    /* ── Divider line ── */
    .section-divider {
      height: 1px;
      background: var(--border);
      margin: 4px 0 20px;
    }

    /* ── Footer ── */
    .footer {
      text-align: center;
      font-size: .7rem;
      color: var(--text-3);
      padding: 20px 32px 0;
      max-width: 1400px;
      margin: 0 auto;
      width: 100%;
    }

    /* ── Responsive ── */
    @media (max-width: 900px) {
      .cards-row { grid-template-columns: 1fr 1fr; }
    }
    @media (max-width: 600px) {
      .main { padding: 16px 14px 0; }
      .header { padding: 14px 16px; }
      .cards-row { grid-template-columns: 1fr; }
      .card-value { font-size: 2.4rem; }
    }

    /* ── Pulse animation for live dot ── */
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: .4; }
    }
    .refresh-dot.online { animation: pulse 2s infinite; }
  </style>
</head>
<body>

<!-- ===== HEADER ===== -->
<header class="header">
  <div class="header-left">
    <div class="header-icon">
      <svg viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
    </div>
    <div>
      <div class="header-title">Patient Health Monitoring Dashboard</div>
      <div class="header-subtitle">Prototipe Monitoring Tanda-Tanda Vital Real-Time</div>
    </div>
  </div>
  <div class="header-right">
    <div class="refresh-time">
      <span class="refresh-dot" id="statusDot"></span>
      Data refreshed at <span id="refreshTime">--:--:--</span>
    </div>
    <span class="device-badge" id="deviceBadge">⏳ Menunggu Perangkat</span>
  </div>
</header>

<!-- ===== MAIN ===== -->
<main class="main">

  <!-- Vital Cards -->
  <div class="cards-row">

    <!-- BPM -->
    <div class="card">
      <div class="card-label-row">
        <div class="card-icon bpm">❤️</div>
        <span class="card-category">Heart Rate</span>
      </div>
      <div class="card-value" id="bpmValue">-- <span class="unit">bpm</span></div>
      <div class="card-desc">Detak Jantung</div>
      <span class="status-chip waiting" id="bpmChip">Menunggu</span>
    </div>

    <!-- SpO2 -->
    <div class="card">
      <div class="card-label-row">
        <div class="card-icon spo2">🩸</div>
        <span class="card-category">SpO₂</span>
      </div>
      <div class="card-value" id="spo2Value">-- <span class="unit">%</span></div>
      <div class="card-desc">Saturasi Oksigen</div>
      <span class="status-chip waiting" id="spo2Chip">Menunggu</span>
    </div>

    <!-- Patient Status -->
    <div class="card">
      <div class="card-label-row">
        <div class="card-icon status">🏥</div>
        <span class="card-category">Status Pasien</span>
      </div>
      <div class="patient-status-value waiting" id="patientStatus">TUNGGU</div>
      <div class="card-desc">Kondisi keseluruhan pasien</div>
      <span class="status-chip waiting" id="patientChip">Belum ada data</span>
    </div>

  </div><!-- /cards-row -->

  <div class="section-divider"></div>

  <!-- ECG Card -->
  <div class="ecg-card">
    <div class="ecg-header">
      <div class="ecg-title-group">
        <div class="ecg-title">ECG Waveform</div>
        <div class="ecg-subtitle">Single-Lead ECG · AD8232 Sensor</div>
      </div>
      <span class="ecg-badge">Live</span>
    </div>
    <div class="ecg-canvas-wrap">
      <canvas id="ecgChart"></canvas>
    </div>
  </div>

</main>

<footer class="footer">
  ⚠️ Sistem prototipe — bukan alat medis klinis. Data real-time, tidak tersimpan. 
  Single-lead ECG (AD8232) · SpO₂ (MAX30102) · Berjalan via Flask + Socket.IO
</footer>

<!-- ===== SCRIPTS ===== -->
<script>
const MAX_POINTS = 350;
const ecgData = new Array(MAX_POINTS).fill(0);
const ecgLabels = new Array(MAX_POINTS).fill('');

const ctx = document.getElementById('ecgChart').getContext('2d');
const ecgChart = new Chart(ctx, {
  type: 'line',
  data: {
    labels: ecgLabels,
    datasets: [{
      data: ecgData,
      borderColor: '#1A7FDB',
      borderWidth: 1.8,
      pointRadius: 0,
      tension: 0.3,
      fill: {
        target: 'origin',
        above: 'rgba(26,127,219,0.06)',
        below: 'rgba(26,127,219,0.02)',
      },
    }]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: 'nearest', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { enabled: false },
    },
    scales: {
      x: {
        display: false,
        grid: { display: false },
      },
      y: {
        grid: {
          color: 'rgba(0,0,0,.05)',
          drawBorder: false,
        },
        ticks: {
          color: '#8C95A6',
          font: { family: 'DM Mono', size: 10 },
          maxTicksLimit: 5,
        },
        border: { display: false },
      }
    }
  }
});

function bpmStatus(v) {
  if (v === null || v === undefined) return ['Menunggu','waiting'];
  if (v < 60) return ['RENDAH','low'];
  if (v > 100) return ['TINGGI','high'];
  return ['AMAN','normal'];
}
function spo2Status(v) {
  if (v === null || v === undefined) return ['Menunggu','waiting'];
  if (v < 95) return ['RENDAH','low'];
  return ['NORMAL','normal'];
}
function patientStatusFn(bpm, spo2) {
  if (bpm === null || spo2 === null || bpm === undefined || spo2 === undefined)
    return ['TUNGGU','waiting','Belum ada data'];
  if (bpm >= 60 && bpm <= 100 && spo2 >= 95)
    return ['AMAN','normal','Kondisi stabil'];
  return ['PERHATIAN','attention','Memerlukan perhatian'];
}
function setChip(el, text, cls) {
  el.textContent = text;
  el.className = 'status-chip ' + cls;
}

const socket = io();

socket.on('connect', () => {
  console.log('[Socket] Terhubung ke server');
});

socket.on('sensor_data', (d) => {
  document.getElementById('refreshTime').textContent = d.timestamp || '--:--:--';

  const dot = document.getElementById('statusDot');
  const badge = document.getElementById('deviceBadge');
  dot.className = 'refresh-dot online';
  badge.textContent = '✅ Perangkat Online';
  badge.className = 'device-badge online';

  const bpmEl = document.getElementById('bpmValue');
  bpmEl.innerHTML = (d.bpm !== null && d.bpm !== undefined)
    ? d.bpm + ' <span class="unit">bpm</span>'
    : '-- <span class="unit">bpm</span>';
  const [bLabel, bCls] = bpmStatus(d.bpm);
  setChip(document.getElementById('bpmChip'), bLabel, bCls);

  const spo2El = document.getElementById('spo2Value');
  spo2El.innerHTML = (d.spo2 !== null && d.spo2 !== undefined)
    ? d.spo2 + ' <span class="unit">%</span>'
    : '-- <span class="unit">%</span>';
  const [sLabel, sCls] = spo2Status(d.spo2);
  setChip(document.getElementById('spo2Chip'), sLabel, sCls);

  const [pLabel, pCls, pDesc] = patientStatusFn(d.bpm, d.spo2);
  const psEl = document.getElementById('patientStatus');
  psEl.textContent = pLabel;
  psEl.className = 'patient-status-value ' + pCls;
  setChip(document.getElementById('patientChip'), pDesc, pCls);

  ecgData.shift();
  ecgData.push(d.ecg);
  ecgChart.update('none');
});

socket.on('device_status', (d) => {
  const dot   = document.getElementById('statusDot');
  const badge = document.getElementById('deviceBadge');
  if (d.connected) {
    dot.className = 'refresh-dot online';
    badge.textContent = '✅ Perangkat Online';
    badge.className = 'device-badge online';
  } else {
    dot.className = 'refresh-dot offline';
    badge.textContent = '❌ Perangkat Offline';
    badge.className = 'device-badge offline';
  }
});

socket.on('disconnect', () => {
  document.getElementById('statusDot').className = 'refresh-dot offline';
  document.getElementById('deviceBadge').textContent = '❌ Koneksi Terputus';
  document.getElementById('deviceBadge').className = 'device-badge offline';
});
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@socketio.on("connect")
def handle_connect():
    """Kirim data terakhir saat client baru terhubung."""
    emit("sensor_data", latest_data)
    emit("device_status", {"connected": latest_data.get("connected", False)})


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  Patient Health Monitoring Dashboard")
    print("=" * 55)
    if DEMO_MODE:
        print("  Mode: DEMO (data simulasi tanpa ESP32)")
        t = threading.Thread(target=demo_reader, daemon=True)
    else:
        print(f"  Mode: REAL — Serial {SERIAL_PORT} @ {BAUD_RATE}")
        t = threading.Thread(target=serial_reader, daemon=True)
    t.start()
    print("  Buka browser: http://localhost:5000")
    print("=" * 55)
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
