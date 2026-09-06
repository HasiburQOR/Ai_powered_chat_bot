from django.http import HttpResponse

def index(request):
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Customer Support Chatbot — Phase 1</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100vh;
            margin: 0;
        }
        .card {
            background: #1e293b;
            padding: 2.5rem;
            border-radius: 1rem;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
            text-align: center;
            max-width: 480px;
            border: 1px solid #334155;
        }
        .status-badge {
            display: inline-block;
            background: #10b981;
            color: #022c22;
            font-weight: 700;
            padding: 0.35rem 0.85rem;
            border-radius: 9999px;
            font-size: 0.875rem;
            margin-bottom: 1.25rem;
        }
        h1 {
            margin: 0 0 0.5rem 0;
            font-size: 1.75rem;
        }
        p {
            color: #94a3b8;
            margin: 0 0 1.5rem 0;
            line-height: 1.5;
        }
        .services {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 0.75rem;
            text-align: left;
        }
        .service-item {
            background: #0f172a;
            padding: 0.75rem;
            border-radius: 0.5rem;
            border: 1px solid #334155;
            font-size: 0.875rem;
        }
        .service-name {
            font-weight: 600;
            color: #cbd5e1;
        }
        .service-status {
            color: #34d399;
            font-size: 0.75rem;
        }
    </style>
</head>
<body>
    <div class="card">
        <div class="status-badge">Phase 1 Complete</div>
        <h1>It works!</h1>
        <p>AI Customer Support Chatbot backend stack is successfully up and running.</p>
        <div class="services">
            <div class="service-item">
                <div class="service-name">Web (Django)</div>
                <div class="service-status">● Running (Gunicorn)</div>
            </div>
            <div class="service-item">
                <div class="service-name">Database</div>
                <div class="service-status">● PostgreSQL + pgvector</div>
            </div>
            <div class="service-item">
                <div class="service-name">Broker</div>
                <div class="service-status">● Redis 7</div>
            </div>
            <div class="service-item">
                <div class="service-name">Celery</div>
                <div class="service-status">● Worker & Beat active</div>
            </div>
        </div>
    </div>
</body>
</html>"""
    return HttpResponse(html_content)
