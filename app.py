import hashlib
import json
import os
import re
import sqlite3
from typing import Any
from typing import Callable
from typing import cast

import nmap
import openai

from dotenv import load_dotenv
from flask import Flask
from flask import render_template
from flask_restful import Api
from flask_restful import Resource

load_dotenv()
openai.api_key = os.getenv('API_KEY')
model_engine = "text-davinci-003"

app = Flask(__name__)
api = Api(app)

nm = nmap.PortScanner()


# Index and Docx page
@app.route('/', methods=['GET'])
def home() -> Any:
    return render_template("index.html")


@app.route('/doc', methods=['GET'])
def doc() -> Any:
    return render_template("doc.html")


@app.route('/register/<int:user_id>/<string:password>/<string:unique_key>')
def store_auth_key(user_id: int, password: str, unique_key: str) -> str:
    sanitized_username = user_id
    sanitized_passwd = password
    sanitized_key = unique_key
    # Hash the user's ID, password, and unique key together
    hash = hashlib.sha256()
    hash.update(str(sanitized_username).encode('utf-8'))
    hash.update(sanitized_passwd.encode('utf-8'))
    hash.update(sanitized_key.encode('utf-8'))
    # Use the hash to generate the auth key
    auth_key = hash.hexdigest()[:20]  # Get the first 20 characters
    db_file = 'auth_keys.db'
    need_create_table = not os.path.exists(db_file)
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    if need_create_table:
        cursor.execute('''CREATE TABLE auth_keys
                        (user_id INT PRIMARY KEY NOT NULL,
                        auth_key TEXT NOT NULL,
                        unique_key TEXT NOT NULL);''')
    query = (
        "INSERT INTO auth_keys "
        "(user_id, auth_key, unique_key) "
        "VALUES (?, ?, ?)"
    )
    cursor.execute(
        query,
        (sanitized_username, auth_key, sanitized_key)
    )

    conn.commit()
    conn.close()

    return auth_key


def to_int(s: str) -> int:
    return int(s)


def sanitize(input_string: str) -> str:
    if not re.match("^[a-zA-Z0-9]*$", input_string):
        raise ValueError("Invalid characters in string")
    else:
        return input_string


def chunk_output(scan_output: dict,
                 max_token_size: int) -> list[dict[str, Any]]:
    output_chunks = []
    current_chunk = {}
    current_token_count = 0

    # Convert JSON to AI usable chunks
    for ip, scan_data in scan_output.items():
        new_data_token_count = len(json.dumps({ip: scan_data}).split())

        if current_token_count + new_data_token_count <= max_token_size:
            current_chunk[ip] = scan_data
            current_token_count += new_data_token_count
        else:
            output_chunks.append(current_chunk)
            current_chunk = {ip: scan_data}
            current_token_count = new_data_token_count
    # The Chunks list that is returned
    if current_chunk:
        output_chunks.append(current_chunk)

    return output_chunks


def AI(analize: str) -> dict[str, Any]:
    # Prompt about what the query is all about
    prompt = f"""
        Do a vulnerability analysis report on the following JSON data and
        follow the following rules:
        1) Calculate the criticality score.
        2) Return all the open ports within the open_ports list.
        3) Return all the closed ports within the closed_ports list.
        4) Return all the filtered ports within the filtered_ports list.

        output format: {{
            "open_ports": [],
            "closed_ports": [],
            "filtered_ports": [],
            "criticality_score": ""
            }}

        data = {analize}
    """
    try:
        # A structure for the request
        completion = openai.Completion.create(
            engine=model_engine,
            prompt=prompt,
            max_tokens=1024,
            n=1,
            stop=None,
        )
        response = completion.choices[0]['text']

        # Assuming extract_ai_output returns a dictionary
        extracted_data = extract_ai_output(response)
    except KeyboardInterrupt:
        print("Bye")
        quit()

    # Store outputs in a dictionary
    ai_output = {
        "open_ports": extracted_data.get("open_ports"),
        "closed_ports": extracted_data.get("closed_ports"),
        "filtered_ports": extracted_data.get("filtered_ports"),
        "criticality_score": extracted_data.get("criticality_score")
    }

    return ai_output


def authenticate(auth_key: str) -> bool:
    conn = sqlite3.connect('auth_keys.db')
    cursor = conn.cursor()
    key = sanitize(auth_key)
    # Check if the given auth_key exists in the database
    cursor.execute("SELECT 1 FROM auth_keys WHERE auth_key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    # If the auth_key is found, return True, else False
    if row:
        return True
    else:
        return False


def extract_ai_output(ai_output: str) -> dict[str, Any]:
    result = {
        "open_ports": [],
        "closed_ports": [],
        "filtered_ports": [],
        "criticality_score": ""
    }

    # Match and extract ports
    open_ports_match = re.search(r'"open_ports": \[([^\]]*)\]', ai_output)
    closed_ports_match = re.search(r'"closed_ports": \[([^\]]*)\]', ai_output)
    filtered_ports_match = re.search(
        r'"filtered_ports": \[([^\]]*)\]', ai_output)

    # If found, convert string of ports to list
    if open_ports_match:
        result["open_ports"] = list(
            map(cast(Callable[[Any], str], int),
                open_ports_match.group(1).split(',')))
    if closed_ports_match:
        result["closed_ports"] = list(
            map(cast(Callable[[Any], str], int),
                closed_ports_match.group(1).split(',')))
    if filtered_ports_match:
        result["filtered_ports"] = list(
            map(cast(Callable[[Any], str], int),
                filtered_ports_match.group(1).split(',')))

    # Match and extract criticality score
    criticality_score_match = re.search(
        r'"criticality_score": "([^"]*)"', ai_output)
    if criticality_score_match:
        result["criticality_score"] = criticality_score_match.group(1)

    return result


def profile(auth: str, url: str, argument: str) -> dict[str, Any]:
    ip = url
    # Nmap Execution command
    usernamecheck = authenticate(auth)
    if usernamecheck is False:
        return {"error": "passwd or username error"}
    else:
        nm.scan('{}'.format(ip), arguments='{}'.format(argument))
        scan_data = nm.analyse_nmap_xml_scan()
        analyze = scan_data["scan"]
        chunk_data = str(chunk_output(analyze, 500))
        all_outputs = []
        for chunks in chunk_data:
            string_chunks = str(chunks)
            data = AI(string_chunks)
            all_outputs.append(data)
        return json.dumps(all_outputs)


# Effective  Scan
class p1(Resource):
    def get(self, auth, url):
        argument = '-Pn -sV -T4 -O -F'
        scan = profile(auth, url, argument)
        return scan


# Simple Scan
class p2(Resource):
    def get(self, auth, url):
        argument = '-Pn -T4 -A -v'
        scan = profile(auth, url, argument)
        return scan


# Low Power Scan
class p3(Resource):
    def get(self, auth, url):
        argument = '-Pn -sS -sU -T4 -A -v'
        scan = profile(auth, url, argument)
        return scan


# partial Intense Scan
class p4(Resource):
    def get(self, auth, url):
        argument = '-Pn -p- -T4 -A -v'
        scan = profile(auth, url, argument)
        return scan


# Complete Intense scan
class p5(Resource):
    def get(self, auth, url):
        argument = '-Pn -sS -sU -T4 -A -PE -PP -PY -g 53 --script=vuln'
        scan = profile(auth, url, argument)
        return scan


api.add_resource(
    p1, "/api/p1/<string:auth>/<string:url>")
api.add_resource(
    p2, "/api/p2/<string:auth>/<string:url>")
api.add_resource(
    p3, "/api/p3/<string:auth>/<string:url>")
api.add_resource(
    p4, "/api/p4/<string:auth>/<string:url>")
api.add_resource(
    p5, "/api/p5/<string:auth>/<string:url>")


if __name__ == '__main__':
    app.run(host="0.0.0.0", port="80")
