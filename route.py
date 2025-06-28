from aiohttp import web
import psutil
import platform
from datetime import datetime

routes = web.RouteTableDef()

def get_system_stats():
    """Récupère les statistiques système"""
    cpu_percent = psutil.cpu_percent()
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    boot_time = datetime.fromtimestamp(psutil.boot_time())

    stats = {
        "system": {
            "os": platform.system(),
            "hostname": platform.node(),
            "uptime": str(datetime.now() - boot_time)
        },
        "cpu": f"{cpu_percent}%",
        "memory": {
            "total": f"{memory.total / (1024**3):.2f} GB",
            "used": f"{memory.used / (1024**3):.2f} GB",
            "percent": f"{memory.percent}%"
        },
        "disk": {
            "total": f"{disk.total / (1024**3):.2f} GB",
            "used": f"{disk.used / (1024**3):.2f} GB",
            "percent": f"{disk.percent}%"
        },
        "connections": {
            "up": len([conn for conn in psutil.net_connections() if conn.status == 'ESTABLISHED']),
            "down": len([conn for conn in psutil.net_connections() if conn.status == 'TIME_WAIT'])
        }
    }
    return stats

def generate_html(stats):
    """Génère le HTML du dashboard"""
    return f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>HyoshCoder Monitoring</title>
        <style>
            :root {{
                --primary: #4361ee;
                --danger: #f72585;
                --success: #4cc9f0;
                --warning: #f8961e;
                --dark: #212529;
            }}
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                line-height: 1.6;
                color: #333;
                max-width: 1200px;
                margin: 0 auto;
                padding: 20px;
                background-color: #f8f9fa;
            }}
            header {{
                text-align: center;
                margin-bottom: 30px;
            }}
            h1 {{
                color: var(--primary);
            }}
            .dashboard {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 20px;
            }}
            .card {{
                background: white;
                border-radius: 10px;
                padding: 20px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            }}
            .card h2 {{
                margin-top: 0;
                color: var(--dark);
                border-bottom: 2px solid #eee;
                padding-bottom: 10px;
            }}
            .status {{
                display: flex;
                align-items: center;
                margin: 10px 0;
            }}
            .status-indicator {{
                width: 15px;
                height: 15px;
                border-radius: 50%;
                margin-right: 10px;
            }}
            .up {{
                background-color: var(--success);
            }}
            .down {{
                background-color: var(--danger);
            }}
            .progress-container {{
                width: 100%;
                background-color: #e9ecef;
                border-radius: 5px;
                margin: 10px 0;
            }}
            .progress-bar {{
                height: 20px;
                border-radius: 5px;
                background-color: var(--primary);
                text-align: center;
                color: white;
                line-height: 20px;
                font-size: 12px;
            }}
            .footer {{
                text-align: center;
                margin-top: 30px;
                color: #6c757d;
            }}
        </style>
    </head>
    <body>
        <header>
            <h1>🚀 HyoshCoder Monitoring</h1>
            <p>Dernière mise à jour: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </header>

        <div class="dashboard">
            <div class="card">
                <h2>📊 Système</h2>
                <p><strong>OS:</strong> {stats['system']['os']}</p>
                <p><strong>Hostname:</strong> {stats['system']['hostname']}</p>
                <p><strong>Uptime:</strong> {stats['system']['uptime']}</p>
            </div>

            <div class="card">
                <h2>🧠 CPU</h2>
                <div class="progress-container">
                    <div class="progress-bar" style="width: {stats['cpu']}">{stats['cpu']}</div>
                </div>
            </div>

            <div class="card">
                <h2>💾 Mémoire</h2>
                <p>Utilisation: {stats['memory']['percent']}</p>
                <div class="progress-container">
                    <div class="progress-bar" style="width: {stats['memory']['percent']}">{stats['memory']['percent']}</div>
                </div>
                <p>Total: {stats['memory']['total']}</p>
                <p>Utilisé: {stats['memory']['used']}</p>
            </div>

            <div class="card">
                <h2>💽 Disque</h2>
                <p>Utilisation: {stats['disk']['percent']}</p>
                <div class="progress-container">
                    <div class="progress-bar" style="width: {stats['disk']['percent']}">{stats['disk']['percent']}</div>
                </div>
                <p>Total: {stats['disk']['total']}</p>
                <p>Utilisé: {stats['disk']['used']}</p>
            </div>

            <div class="card">
                <h2>🌐 Connexions</h2>
                <div class="status">
                    <div class="status-indicator up"></div>
                    <span>UP: {stats['connections']['up']}</span>
                </div>
                <div class="status">
                    <div class="status-indicator down"></div>
                    <span>DOWN: {stats['connections']['down']}</span>
                </div>
            </div>
        </div>

        <div class="footer">
            <p>© {datetime.now().year} HyoshCoder Bots - Monitoring System</p>
        </div>
    </body>
    </html>
    """

@routes.get("/", allow_head=True)
async def root_route_handler(request):
    stats = get_system_stats()
    return web.Response(
        text=generate_html(stats),
        content_type='text/html'
    )

async def web_server():
    web_app = web.Application(client_max_size=30000000)
    web_app.add_routes(routes)
    return web_app