# -*- coding: utf-8 -*-
"""Запуск системы для разработки/установки.

Windows (без Gunicorn):  python run.py --waitress
Linux/Proxmox (LXC/VM):  gunicorn -c gunicorn.conf.py wsgi:app
"""
import argparse

from app import create_app


def main():
    p = argparse.ArgumentParser(description="DocCheck — система проверки документации")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--waitress", action="store_true",
                   help="использовать сервер waitress (для Windows)")
    p.add_argument("--debug", action="store_true")
    a = p.parse_args()

    app = create_app()
    print(f"DocCheck запущен: http://{a.host}:{a.port}  "
          f"(админ по умолчанию: login=admin, пароль — см. ADMIN_PASSWORD в .env)")
    if a.waitress:
        from waitress import serve
        serve(app, host=a.host, port=a.port, threads=8)
    else:
        app.run(host=a.host, port=a.port, debug=a.debug)


if __name__ == "__main__":
    main()
