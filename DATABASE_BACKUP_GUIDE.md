# Database Backup & Restore Guide

## Current Setup
- **Live Trading Mode**: Active with fresh database
- **Sandbox Backup**: Saved in Docker volume `coin_dbdata_sandbox_backup`

## Switch Between Modes

### Option 1: Quick Switch (keeps database)
Just swap the .env file:
```bash
# Switch to sandbox
cp .env.sandbox .env
docker-compose restart

# Switch to live
cp .env.live .env
docker-compose restart
```

### Option 2: Full Switch (with database restore)

**Restore Sandbox Mode:**
```bash
# 1. Copy sandbox config
cp .env.sandbox .env

# 2. Stop containers
docker-compose down

# 3. Restore sandbox database
docker volume rm coin_dbdata
docker volume create coin_dbdata
docker run --rm \
  -v coin_dbdata_sandbox_backup:/source \
  -v coin_dbdata:/target \
  alpine sh -c "cp -a /source/. /target/"

# 4. Start with sandbox data
docker-compose up -d
```

**Save Current Live Data & Switch:**
```bash
# 1. Stop containers
docker-compose down

# 2. Backup live database
docker volume create coin_dbdata_live_backup
docker run --rm \
  -v coin_dbdata:/source \
  -v coin_dbdata_live_backup:/backup \
  alpine sh -c "cp -a /source/. /backup/"

# 3. Restore sandbox and switch config
docker volume rm coin_dbdata
docker volume create coin_dbdata
docker run --rm \
  -v coin_dbdata_sandbox_backup:/source \
  -v coin_dbdata:/target \
  alpine sh -c "cp -a /source/. /target/"
cp .env.sandbox .env

# 4. Start
docker-compose up -d
```

## Database Volumes

### List all backups:
```bash
docker volume ls | grep coin
```

### Inspect a backup:
```bash
docker run --rm -v coin_dbdata_sandbox_backup:/data alpine ls -lah /data
```

### Delete a backup (if needed):
```bash
docker volume rm coin_dbdata_sandbox_backup
```

## Current Configuration Files
- `.env` - Active configuration (currently LIVE mode)
- `.env.sandbox` - Sandbox configuration backup
- `.env.live` - Live configuration backup (if you create it)

## Safety Notes
⚠️ **ALWAYS verify APP_MODE and OKX_USE_SANDBOX before starting:**
```bash
cat .env | grep -E "APP_MODE|OKX_USE_SANDBOX"
```

Expected output:
- **Sandbox**: `APP_MODE=sandbox` + `OKX_USE_SANDBOX=true`
- **Live**: `APP_MODE=live` + `OKX_USE_SANDBOX=false`
