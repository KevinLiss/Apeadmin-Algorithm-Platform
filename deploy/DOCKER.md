# =============================================================================
# Docker 部署说明
#
# 本配置参考 cosmo-edge 项目设计，实现一键容器化部署
# =============================================================================

## 快速开始

### 1. 准备环境

确保已安装：
- Docker Engine 20.10+
- Docker Compose 2.0+

```bash
# 验证安装
docker --version
docker-compose --version
```

### 2. 配置环境变量

```bash
# 复制示例配置
cp .env.docker .env

# 编辑配置（必改项：JWT_SECRET, DB_PASSWORD）
vim .env
```

### 3. 启动服务

```bash
# 首次构建并启动
docker-compose up -d

# 查看日志
docker-compose logs -f backend

# 停止服务
docker-compose down
```

### 4. 访问应用

- 后端 API: http://localhost:8000
- API 文档: http://localhost:8000/docs
- 管理后台: http://localhost:8000/admin/

## 服务架构

```
┌─────────────────────────────────────────────────────────────┐
│                      Docker Network                         │
│                      apeadmin-network                       │
│                                                              │
│  ┌──────────┐   ┌──────────┐   ┌──────────────────────┐   │
│  │  MySQL   │   │  Redis   │   │       Backend       │   │
│  │  :3306   │   │  :6379   │   │    FastAPI :8000    │   │
│  └──────────┘   └──────────┘   │  (含前端静态文件)     │   │
│                                └──────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
            │                       │
            │                       │
            ▼                       ▼
      [可选]                   [可选]
      Nginx:80                 Nginx:80
```

## 常用命令

```bash
# 重启后端（代码修改后）
docker-compose build backend
docker-compose up -d backend

# 查看后端日志
docker-compose logs -f backend

# 进入后端容器（调试）
docker-compose exec backend bash

# 数据库备份
docker-compose exec mysql mysqldump -uapeadmin -p apeadmin > backup.sql

# 清理数据（慎用）
docker-compose down -v
```

## 数据持久化

| 路径 | 说明 |
|------|------|
| `mysql_data` | MySQL 数据文件 |
| `redis_data` | Redis 持久化数据 |
| `backend_models` | AI 模型文件 |
| `backend_uploads` | 上传的文件 |
| `backend_logs` | 运行日志 |

## 扩展

### 使用 Nginx 反向代理

编辑 `docker-compose.yml`，取消 nginx 服务的注释：

```yaml
nginx:
  image: nginx:alpine
  volumes:
    - ./deploy/docker/nginx.conf:/etc/nginx/nginx.conf:ro
  ports:
    - "8080:80"
```

然后访问 http://localhost:8080

### 添加 HTTPS

1. 使用 Let's Encrypt 或购买证书
2. 修改 nginx.conf 添加 SSL 配置
3. 重启服务

## 参考

- cosmo-edge: https://github.com/cosmo-wander-ai/cosmo-edge
- Docker 部署最佳实践
