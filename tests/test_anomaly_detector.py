from app.anomaly_detector import detect
def test_execution_anomalies_surface():
 x=detect(300,100,.5,.1,.1);assert "LATENCY_SPIKE" in x and "SLIPPAGE_SPIKE" in x and "INCIDENT_RATE_HIGH" in x
