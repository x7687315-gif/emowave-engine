#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EmoWave Dashboard — Flask backend for emotion engine visualization.
"""

import json
import os

from flask import Flask, jsonify, render_template, send_from_directory

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)

DATA_PATH = os.path.join(app.root_path, "static", "data", "simulation_frames.json")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/mobile")
def mobile():
    return render_template("mobile.html")


@app.route("/api/data")
def api_data():
    if not os.path.exists(DATA_PATH):
        return (
            jsonify(
                {
                    "error": " simulation_frames.json 不存在，请先运行模拟生成数据。",
                    "hint": "Expected path: {}".format(DATA_PATH),
                }
            ),
            404,
        )
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data)
    except json.JSONDecodeError as e:
        return jsonify({"error": "JSON 解析失败", "detail": str(e)}), 500
    except Exception as e:
        return jsonify({"error": "读取数据失败", "detail": str(e)}), 500


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok"})


@app.route("/static/js/<path:filename>")
def serve_js(filename):
    return send_from_directory(os.path.join(app.root_path, "static", "js"), filename)


@app.route("/static/css/<path:filename>")
def serve_css(filename):
    return send_from_directory(os.path.join(app.root_path, "static", "css"), filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
