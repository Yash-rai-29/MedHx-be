from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from common_code.config import settings

# Import routers
from doctor_service.auth.auth_router import router as auth_router
from doctor_service.patients.patients_router import router as patients_router
from doctor_service.consultations.consultations_router import router as consultations_router
from doctor_service.medication_safety.safety_router import router as safety_router
from doctor_service.chatbot.chatbot_router import router as chatbot_router
from doctor_service.analytics.analytics_router import router as analytics_router

def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Health Companion — Doctor Backend",
        description="FastAPI service serving Doctor Web Console applications",
        version="1.0.0"
    )
    
    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Include routers
    app.include_router(auth_router, prefix="/auth", tags=["Doctor Authentication"])
    app.include_router(patients_router, prefix="/patients", tags=["Patient Management"])
    app.include_router(consultations_router, prefix="/consultations", tags=["Consultation Workspaces"])
    app.include_router(safety_router, prefix="/medication-safety", tags=["Medication Safety Engine"])
    app.include_router(chatbot_router, prefix="/chatbot", tags=["Doctor Assistant Chatbot"])
    app.include_router(analytics_router, prefix="/analytics", tags=["Population Health Trends"])
    
    @app.get("/health", tags=["Health"])
    async def health_check():
        return {
            "status": "healthy",
            "service": "doctor-service",
            "environment": settings.ENVIRONMENT
        }
        
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def landing_page():
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Health Companion — Doctor Backend</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-color: #0b0f19;
            --card-bg: rgba(17, 24, 39, 0.7);
            --border-color: rgba(255, 255, 255, 0.08);
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
            --accent-primary: #8b5cf6;
            --accent-secondary: #ec4899;
            --success-color: #10b981;
        }}
        
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        
        body {{
            font-family: 'Outfit', sans-serif;
            background-color: var(--bg-color);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow-x: hidden;
            position: relative;
        }}
        
        body::before {{
            content: '';
            position: absolute;
            width: 40vw;
            height: 40vw;
            top: -10vw;
            left: -10vw;
            background: radial-gradient(circle, rgba(139, 92, 246, 0.15) 0%, rgba(0,0,0,0) 70%);
            z-index: 0;
            pointer-events: none;
        }}
        
        body::after {{
            content: '';
            position: absolute;
            width: 45vw;
            height: 45vw;
            bottom: -15vw;
            right: -15vw;
            background: radial-gradient(circle, rgba(236, 72, 153, 0.15) 0%, rgba(0,0,0,0) 70%);
            z-index: 0;
            pointer-events: none;
        }}
        
        .container {{
            position: relative;
            z-index: 10;
            width: 100%;
            max-width: 680px;
            padding: 24px;
            animation: fadeIn 0.8s cubic-bezier(0.16, 1, 0.3, 1);
        }}
        
        .card {{
            background: var(--card-bg);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--border-color);
            border-radius: 24px;
            padding: 40px;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3), 
                        inset 0 1px 1px rgba(255, 255, 255, 0.1);
            position: relative;
        }}
        
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 32px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            padding-bottom: 24px;
        }}
        
        .logo-area h1 {{
            font-size: 24px;
            font-weight: 700;
            letter-spacing: -0.5px;
            background: linear-gradient(135deg, #fff 0%, #f472b6 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 6px;
        }}
        
        .logo-area p {{
            font-size: 14px;
            color: var(--text-secondary);
            font-weight: 400;
        }}
        
        .status-badge {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(16, 185, 129, 0.1);
            border: 1px solid rgba(16, 185, 129, 0.2);
            color: var(--success-color);
            padding: 6px 14px;
            border-radius: 30px;
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }}
        
        .status-dot {{
            width: 8px;
            height: 8px;
            background-color: var(--success-color);
            border-radius: 50%;
            box-shadow: 0 0 12px var(--success-color);
            animation: pulse 1.8s infinite;
        }}
        
        .meta-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 16px;
            margin-bottom: 36px;
        }}
        
        .meta-item {{
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid rgba(255, 255, 255, 0.03);
            border-radius: 14px;
            padding: 14px 16px;
            transition: all 0.3s ease;
        }}
        
        .meta-item:hover {{
            background: rgba(255, 255, 255, 0.04);
            border-color: rgba(255, 255, 255, 0.06);
            transform: translateY(-2px);
        }}
        
        .meta-label {{
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--text-secondary);
            margin-bottom: 4px;
            font-weight: 500;
        }}
        
        .meta-value {{
            font-size: 14px;
            font-weight: 600;
            color: var(--text-primary);
            word-break: break-all;
        }}
        
        .actions {{
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}
        
        @media(min-width: 480px) {{
            .actions {{
                flex-direction: row;
            }}
        }}
        
        .btn {{
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            padding: 14px 24px;
            border-radius: 14px;
            font-size: 14px;
            font-weight: 600;
            text-decoration: none;
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            cursor: pointer;
        }}
        
        .btn-primary {{
            background: linear-gradient(135deg, var(--accent-primary) 0%, var(--accent-secondary) 100%);
            color: #ffffff;
            border: none;
            box-shadow: 0 4px 15px rgba(139, 92, 246, 0.25);
        }}
        
        .btn-primary:hover {{
            box-shadow: 0 6px 20px rgba(139, 92, 246, 0.4);
            transform: translateY(-2px);
        }}
        
        .btn-secondary {{
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-primary);
            border: 1px solid rgba(255, 255, 255, 0.08);
        }}
        
        .btn-secondary:hover {{
            background: rgba(255, 255, 255, 0.08);
            border-color: rgba(255, 255, 255, 0.15);
            transform: translateY(-2px);
        }}
        
        .btn-tertiary {{
            background: transparent;
            color: var(--text-secondary);
            border: 1px solid transparent;
        }}
        
        .btn-tertiary:hover {{
            color: var(--text-primary);
            background: rgba(255, 255, 255, 0.03);
            border-color: rgba(255, 255, 255, 0.05);
        }}

        .footer {{
            margin-top: 32px;
            text-align: center;
            font-size: 11px;
            color: var(--text-secondary);
            opacity: 0.6;
        }}
        
        @keyframes fadeIn {{
            from {{
                opacity: 0;
                transform: translateY(10px);
            }}
            to {{
                opacity: 1;
                transform: translateY(0);
            }}
        }}
        
        @keyframes pulse {{
            0% {{
                transform: scale(0.95);
                box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);
            }}
            70% {{
                transform: scale(1);
                box-shadow: 0 0 0 6px rgba(16, 185, 129, 0);
            }}
            100% {{
                transform: scale(0.95);
                box-shadow: 0 0 0 0 rgba(16, 185, 129, 0);
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <div class="header">
                <div class="logo-area">
                    <h1>AI Health Companion</h1>
                    <p>Doctor Backend Service</p>
                </div>
                <div class="status-badge">
                    <span class="status-dot"></span>
                    Online
                </div>
            </div>
            
            <div class="meta-grid">
                <div class="meta-item">
                    <div class="meta-label">Environment</div>
                    <div class="meta-value">{settings.ENVIRONMENT}</div>
                </div>
                <div class="meta-item">
                    <div class="meta-label">GCP Project ID</div>
                    <div class="meta-value">{settings.GCP_PROJECT_ID}</div>
                </div>
                <div class="meta-item">
                    <div class="meta-label">GCP Region</div>
                    <div class="meta-value">{settings.GCP_REGION}</div>
                </div>
                <div class="meta-item">
                    <div class="meta-label">Storage Bucket</div>
                    <div class="meta-value">{settings.STORAGE_BUCKET_NAME}</div>
                </div>
                <div class="meta-item">
                    <div class="meta-label">Gemini Model</div>
                    <div class="meta-value">{settings.GEMINI_MODEL}</div>
                </div>
                <div class="meta-item">
                    <div class="meta-label">Embedding Model</div>
                    <div class="meta-value">{settings.GEMINI_EMBEDDING_MODEL}</div>
                </div>
            </div>
            
            <div class="actions">
                <a href="/docs" class="btn btn-primary">
                    <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25"></path>
                    </svg>
                    Swagger API Docs
                </a>
                <a href="/redoc" class="btn btn-secondary">
                    <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                    </svg>
                    ReDoc Specs
                </a>
                <a href="/health" class="btn btn-tertiary">
                    <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"></path>
                    </svg>
                    Health JSON
                </a>
            </div>
            
            <div class="footer">
                &copy; 2026 AI Health Companion. Secure healthcare RAG service for India.
            </div>
        </div>
    </div>
</body>
</html>"""
        return HTMLResponse(content=html_content, status_code=200)
        
    return app

app = create_app()
