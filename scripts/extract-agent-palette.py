from pathlib import Path
import json
import struct

folder = Path(__file__).resolve().parents[1] / 'web/implementation/vendor/models'
data = (folder / 'ItEngineer.glb').read_bytes()
json_size = struct.unpack_from('<I', data, 12)[0]
gltf = json.loads(data[20:20 + json_size])
view = gltf['bufferViews'][gltf['images'][0]['bufferView']]
start = 20 + json_size + 8 + view.get('byteOffset', 0)
(folder / 'palette.png').write_bytes(data[start:start + view['byteLength']])
