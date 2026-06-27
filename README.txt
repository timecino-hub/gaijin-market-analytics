# Gaijin Market Analytics

一个用于分析 Gaijin Market 商品历史价格、流动性、风险和潜在收益周期的网站项目。

## 当前状态

项目规划与工程初始化阶段。

## 计划支持的分析周期

- 7 天
- 30 天
- 90 天
- 180 天

## 数据来源原则

第一版仅支持：

- CSV 导入
- JSON 导入
- 人工录入
- 明确获得授权的数据源

本项目不实现未经授权的自动爬虫、自动登录、自动购买、自动出售或自动撤单。

## 计划技术栈

- Frontend: Next.js + TypeScript
- Backend: FastAPI + Python
- Database: PostgreSQL
- Analysis: Pandas / NumPy / LightGBM
- Deployment: Docker Compose