from flask import Flask, jsonify, send_from_directory
import os, scanner

app = Flask(__name__, static_folder="static")

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/data")
def data():
    try:
        return jsonify(scanner.get_data())
    except Exception as e:
        return jsonify(error=str(e)), 503

@app.route("/health")
def health():
    return "ok"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
