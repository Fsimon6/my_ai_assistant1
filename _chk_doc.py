import backend.services.document_service as d
print('IMPORT_OK')
print('ALLOWED_EXT=', sorted(d.ALLOWED_EXT))
print('MIME_KEYS=', sorted(d.ALLOWED_MIME.keys()))
