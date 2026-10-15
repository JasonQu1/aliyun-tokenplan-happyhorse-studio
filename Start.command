#!/bin/zsh
cd "${0:A:h}"
python3 server.py
if [ $? -ne 0 ]; then
  echo "启动失败。请确认已安装 Python 3.9+，或查看上方错误。"
  read "?按回车关闭窗口…"
fi
