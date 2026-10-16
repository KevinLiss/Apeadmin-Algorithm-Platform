# =============================================================================
# ApeAdmin Backend Dockerfile
#
# 构建: docker build -t apeadmin-backend:latest -f backend/Dockerfile .
# 运行: docker run -d -p 8000:8000 --env-file .env apeadmin-backend:latest
# =============================================================================

# 阶段 1: 构建前端
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend

# 安装 pnpm
RUN corepack enable && corepack prepare pnpm@latest --activate

# 复制前端代码
COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN if [ -f pnpm-lock.yaml ]; then pnpm install --frozen-lockfile; else npm install; fi

COPY frontend/ ./
RUN npm run build

# 阶段 2: 构建后端
FROM python:3.11-slim AS backend-builder

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv（快速 Python 包管理）
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# 复制后端代码（不包括 node_modules 等）
COPY backend/pyproject.toml backend/alembic/ ./alembic/
COPY backend/src/ ./src/
COPY backend/scripts/ ./scripts/

# 安装 Python 依赖
RUN uv sync --frozen --no-dev

# 复制前端构建产物到后端静态目录
COPY --from=frontend-builder /app/frontend/dist ./src/apeadmin_web/dist

# 创建非 root 用户（安全）
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["uv", "run", "python", "-m", "src.cli", "run-server", "--host", "0.0.0.0", "--port", "8000"]
