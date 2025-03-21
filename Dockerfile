FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 复制项目文件
COPY . /app/

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 设置环境变量
ENV PYTHONPATH=/app
ENV PORT=8080

# 暴露端口
EXPOSE 8080

# 设置启动命令，支持从环境变量选择要启动的应用
ENV APP_NAME=tradingview_signal_tracker
CMD python apps/${APP_NAME}/main.py 