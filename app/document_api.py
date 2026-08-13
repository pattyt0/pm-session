import base64
import json
import os
import time
import uuid
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote, urlparse

import boto3

TABLE_NAME = os.environ['TABLE_NAME']
BUCKET_NAME = os.environ['BUCKET_NAME']
REGION = os.environ['AWS_REGION']

dynamodb = boto3.resource('dynamodb', region_name=REGION)
s3 = boto3.client('s3', region_name=REGION)
table = dynamodb.Table(TABLE_NAME)

def to_json_safe(value):
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [to_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: to_json_safe(item) for key, item in value.items()}
    return value

class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(to_json_safe(payload)).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get('Content-Length', '0'))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode('utf-8')
        return json.loads(raw)

    def route(self):
        parts = [unquote(part) for part in urlparse(self.path).path.strip('/').split('/') if part]
        if len(parts) == 1 and parts[0] == 'health':
            return 'health', None
        if len(parts) == 1 and parts[0] == 'documentos':
            return 'collection', None
        if len(parts) == 2 and parts[0] == 'documentos':
            return 'item', parts[1]
        return None, None

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
        self.end_headers()

    def do_GET(self):
        route, folio = self.route()
        if route == 'health':
            self.send_json(200, {'message': 'API activa y funcional'})
            return
        if route == 'collection':
            response = table.scan()
            items = response.get('Items', [])
            for item in items:
                item.pop('contentBase64', None)
            self.send_json(200, {'items': items})
            return
        if route == 'item':
            response = table.get_item(Key={'folio': folio})
            item = response.get('Item')
            if not item:
                self.send_json(404, {'message': 'Documento no encontrado'})
                return
            if item.get('s3Key'):
                obj = s3.get_object(Bucket=BUCKET_NAME, Key=item['s3Key'])
                item['contentBase64'] = base64.b64encode(obj['Body'].read()).decode('utf-8')
            self.send_json(200, item)
            return
        self.send_json(404, {'message': 'Ruta no encontrada'})

    def do_POST(self):
        route, _ = self.route()
        if route != 'collection':
            self.send_json(404, {'message': 'Ruta no encontrada'})
            return
        data = self.read_json()
        folio = data.get('folio') or str(uuid.uuid4())
        now = int(time.time())
        item = {
            'folio': folio,
            'nombre': data.get('nombre', folio),
            'descripcion': data.get('descripcion', ''),
            'createdAt': now,
            'updatedAt': now,
        }
        content = data.get('contentBase64')
        if content:
            key = data.get('s3Key') or f'documentos/{folio}'
            s3.put_object(Bucket=BUCKET_NAME, Key=key, Body=base64.b64decode(content))
            item['s3Key'] = key
        table.put_item(Item=item)
        self.send_json(201, item)

    def do_PUT(self):
        route, folio = self.route()
        if route != 'item':
            self.send_json(404, {'message': 'Ruta no encontrada'})
            return
        data = self.read_json()
        existing = table.get_item(Key={'folio': folio}).get('Item')
        if not existing:
            self.send_json(404, {'message': 'Documento no encontrado'})
            return
        item = dict(existing)
        item['nombre'] = data.get('nombre', item.get('nombre', folio))
        item['descripcion'] = data.get('descripcion', item.get('descripcion', ''))
        item['updatedAt'] = int(time.time())
        content = data.get('contentBase64')
        if content:
            key = data.get('s3Key') or item.get('s3Key') or f'documentos/{folio}'
            s3.put_object(Bucket=BUCKET_NAME, Key=key, Body=base64.b64decode(content))
            item['s3Key'] = key
        table.put_item(Item=item)
        self.send_json(200, item)

    def do_DELETE(self):
        route, folio = self.route()
        if route != 'item':
            self.send_json(404, {'message': 'Ruta no encontrada'})
            return
        existing = table.get_item(Key={'folio': folio}).get('Item')
        if not existing:
            self.send_json(404, {'message': 'Documento no encontrado'})
            return
        if existing.get('s3Key'):
            s3.delete_object(Bucket=BUCKET_NAME, Key=existing['s3Key'])
        table.delete_item(Key={'folio': folio})
        self.send_json(200, {'message': 'Documento eliminado', 'folio': folio})

if __name__ == '__main__':
    HTTPServer(('0.0.0.0', 80), Handler).serve_forever()
