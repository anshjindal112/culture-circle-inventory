from flask import Flask
from datetime import datetime
from db.database import init_app
from routes.dashboard import dashboard_bp
from routes.csv_import import csv_bp
from routes.blanks import blanks_bp
from routes.restock import restock_bp
from routes.orders import orders_bp
from routes.sales import sales_bp
from routes.inventory_sync import inventory_sync_bp


def create_app():
    app = Flask(__name__)
    init_app(app)
    app.permanent_session_lifetime = 28800  # 8 hours

    # Template helper: current time for staleness check
    @app.template_global()
    def now():
        return datetime.now()

    # Template helper: map color names to hex for swatches
    COLOR_HEX = {
        'BLACK': '#1a1a1a', 'WHITE': '#f8f8f8', 'RED': '#e74c3c', 'BLUE': '#3498db',
        'NAVY BLUE': '#2c3e6b', 'SKY BLUE': '#87ceeb', 'ICE BLUE': '#b3d9ff',
        'LIGHT BLUE': '#add8e6', 'GREEN': '#27ae60', 'DARK GREEN': '#1e6b3a',
        'FOREST GREEN': '#228b22', 'OLIVE GREEN': '#708238', 'BROWN': '#8b5e3c',
        'DARK BROWN': '#5c3317', 'LIGHT BROWN': '#c4a882', 'BEIGE': '#d4c5a9',
        'CREAM': '#fffdd0', 'SAND': '#c2b280', 'PINK': '#e97fa5', 'BABY PINK': '#f4c2c2',
        'LIGHT PINK': '#ffb6c1', 'GREY': '#95a5a6', 'WINE': '#722f37', 'YELLOW': '#f1c40f',
    }

    @app.template_global()
    def _color_hex(name):
        if not name:
            return '#ddd'
        return COLOR_HEX.get(name.upper().strip(), '#bbb')

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(csv_bp)
    app.register_blueprint(blanks_bp)
    app.register_blueprint(restock_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(sales_bp)
    app.register_blueprint(inventory_sync_bp)

    return app


app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5002)
