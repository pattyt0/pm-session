import base64
import os
import time
import uuid
from decimal import Decimal

import boto3
from flask import Flask, jsonify, request

TABLE_NAME = os.environ['TABLE_NAME']
BUCKET_NAME = os.environ['BUCKET_NAME']
REGION = os.environ['AWS_REGION']

dynamodb = boto3.resource('dynamodb', region_name=REGION)
s3 = boto3.client('s3', region_name=REGION)
table = dynamodb.Table(TABLE_NAME)
app = Flask(__name__)


def to_json_safe(value):
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [to_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: to_json_safe(item) for key, item in value.items()}
    return value


def json_response(payload, status=200):
    response = jsonify(to_json_safe(payload))
    response.status_code = status
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,DELETE,OPTIONS'
    return response


@app.route('/health', methods=['GET'])
def health():
    return json_response({'message': 'API activa y funcional'})


@app.route('/documentos', methods=['GET'])
def list_documents():
    response = table.scan()
    items = response.get('Items', [])
    for item in items:
        item.pop('contentBase64', None)
    return json_response({'items': items})


@app.route('/documentos/<path:folio>', methods=['GET'])
def get_document(folio):
    response = table.get_item(Key={'folio': folio})
    item = response.get('Item')
    if not item:
        return json_response({'message': 'Documento no encontrado'}, 404)
    if item.get('s3Key'):
        obj = s3.get_object(Bucket=BUCKET_NAME, Key=item['s3Key'])
        item['contentBase64'] = base64.b64encode(obj['Body'].read()).decode('utf-8')
    return json_response(item)


@app.route('/documentos', methods=['POST'])
def create_document():
    data = request.get_json(silent=True) or {}
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
    return json_response(item, 201)


@app.route('/documentos/<path:folio>', methods=['PUT'])
def update_document(folio):
    data = request.get_json(silent=True) or {}
    existing = table.get_item(Key={'folio': folio}).get('Item')
    if not existing:
        return json_response({'message': 'Documento no encontrado'}, 404)
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
    return json_response(item)


@app.route('/documentos/<path:folio>', methods=['DELETE'])
def delete_document(folio):
    existing = table.get_item(Key={'folio': folio}).get('Item')
    if not existing:
        return json_response({'message': 'Documento no encontrado'}, 404)
    if existing.get('s3Key'):
        s3.delete_object(Bucket=BUCKET_NAME, Key=existing['s3Key'])
    table.delete_item(Key={'folio': folio})
    return json_response({'message': 'Documento eliminado', 'folio': folio})


@app.errorhandler(404)
def not_found(_error):
    return json_response({'message': 'Ruta no encontrada'}, 404)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
