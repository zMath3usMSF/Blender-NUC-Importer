import bpy
import bmesh
import struct
import os
import ctypes
from mathutils import Vector, Quaternion, Matrix

DBG = True

# ── ea_swizzle.dll ────────────────────────────────────────────────────────────
_ea_swizzle_dll = None

def _load_ea_swizzle():
    global _ea_swizzle_dll
    if _ea_swizzle_dll is not None:
        return _ea_swizzle_dll
    dll_path = os.path.join(os.path.dirname(__file__), "ea_swizzle.dll")
    if not os.path.isfile(dll_path):
        _dbg(f"ea_swizzle.dll não encontrada em {dll_path}")
        return None
    try:
        dll = ctypes.CDLL(dll_path)
        dll.swizzle4.argtypes   = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
        dll.swizzle4.restype    = None
        dll.unswizzle4.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
        dll.unswizzle4.restype  = None
        _ea_swizzle_dll = dll
        _dbg("ea_swizzle.dll carregada com sucesso")
    except Exception as e:
        _dbg(f"Erro ao carregar ea_swizzle.dll: {e}")
    return _ea_swizzle_dll

def _ea_unswizzle4(data: bytes, width: int, height: int) -> bytes:
    dll = _load_ea_swizzle()
    if dll is None:
        return data  # fallback: sem unswizzle
    out = ctypes.create_string_buffer(len(data))
    dll.unswizzle4(data, out, width, height)
    return bytes(out)

def _dbg(*args):
    if DBG:
        print("[SRAW]", *args)

def align4(offset: int) -> int:
    r = offset % 4
    return offset if r == 0 else offset + (4 - r)

_REF = 'ref'

def _is_ref(slot):
    return isinstance(slot, tuple) and len(slot) == 3 and slot[0] == _REF

def _resolve_ref(vertex_list, model_idx, slot_idx, depth=0):
    if depth > 64:
        return None, None
    if model_idx >= len(vertex_list):
        return None, None
    vl = vertex_list[model_idx]
    if slot_idx >= len(vl):
        return None, None
    slot = vl[slot_idx]
    if _is_ref(slot):
        return _resolve_ref(vertex_list, slot[1], slot[2], depth + 1)
    if len(slot) >= 3:
        return model_idx, slot_idx
    return None, None


# ── RawFile ───────────────────────────────────────────────────────────────────

class RawFile:
    def __init__(self, data: bytes):
        self.data = data

    def __len__(self):
        return len(self.data)


def read_raw_files(container: bytes) -> list:
    if len(container) < 4:
        return []
    offset = 0
    files_count = struct.unpack_from('<I', container, offset)[0]; offset += 4
    _dbg(f"filesCount: {files_count}")
    entries = []
    for i in range(files_count):
        if offset + 8 > len(container):
            break
        file_offset = struct.unpack_from('<I', container, offset)[0]; offset += 4
        file_length = struct.unpack_from('<I', container, offset)[0]; offset += 4
        entries.append((file_offset, file_length))
    raw_files = []
    for i, (file_offset, file_length) in enumerate(entries):
        end = file_offset + file_length
        if end > len(container):
            raw_files.append(RawFile(b''))
        else:
            raw_files.append(RawFile(container[file_offset:end]))
    return raw_files


# ── parse_bone_palette ───────────────────────────────────────────────────────
# Lê a bone palette do bone_palette_offset.
# Cada entrada tem 6 bytes: [tipo(uint16)][skin_bone_id][...flags...]
#   tipo 2 = entrada intermediária  → sem flip vertical
#   tipo 3 = entrada terminadora    → flip vertical na mesh desse sub-modelo

def parse_bone_palette(data: bytes, bone_palette_offset: int) -> tuple:
    flat_list  = []   # skin_bone_id por sub-modelo em ordem
    palette    = []   # únicos
    flip_flags = []   # True se tipo 3 (flip vertical Z), False se tipo 2
    seen       = set()

    if bone_palette_offset == 0 or bone_palette_offset + 0x0C > len(data):
        return flat_list, palette, flip_flags

    off = bone_palette_offset + 0x0C

    while off + 6 <= len(data):
        tipo = struct.unpack_from('<H', data, off)[0]
        if tipo == 0:
            break
        elif tipo == 2:
            skin_bone_id  = struct.unpack_from('<H', data, off + 2)[0]
            flip_vertical = False
        elif tipo == 3:
            skin_bone_id  = data[off + 2]
            flip_vertical = True   # tipo 3 → inverte verticalmente a mesh
        else:
            break

        flat_list.append(skin_bone_id)
        flip_flags.append(flip_vertical)
        if skin_bone_id not in seen:
            seen.add(skin_bone_id)
            palette.append(skin_bone_id)

        off += 6

    return flat_list, palette, flip_flags

# ── TextureArray ──────────────────────────────────────────────────────────────

import numpy as np

class PointerEntry:
    __slots__ = ('offset', 'width', 'height', 'height1', 'size', 'bpp')
    def __init__(self):
        self.offset  = 0
        self.width   = 0
        self.height  = 0
        self.height1 = 0
        self.size    = 0
        self.bpp     = 8

class TextureEntry:
    __slots__ = ('pixel_index', 'palette_index', 'width', 'height', 'bpp')
    def __init__(self):
        self.pixel_index   = 0
        self.palette_index = 0
        self.width         = 0
        self.height        = 0
        self.bpp           = 8


def unswizzle8(width, height, data):
    out = bytearray(width * height)

    for y in range(height):
        for x in range(width):
            block_location  = (y & ~0xF) * width + (x & ~0xF) * 2
            swap_selector   = (((y + 2) >> 2) & 1) * 4   # C#: * 4, resultado 0 ou 4
            posY            = (((y & ~3) >> 1) + (y & 1)) & 7
            column_location = posY * width * 2 + ((x + swap_selector) & 7) * 4
            byte_num        = ((y >> 1) & 1) + ((x >> 2) & 2)

            src = block_location + column_location + byte_num
            dst = y * width + x

            if src < len(data):
                out[dst] = data[src]

    return out


def _unswizzle_palette(palette_rgba: list) -> list:
    if len(palette_rgba) != 256:
        return palette_rgba
    out = [None] * 256
    j = 0
    for i in range(0, 256, 32):
        out[i:i+8]    = palette_rgba[j:j+8]
        out[i+16:i+24] = palette_rgba[j+8:j+16]
        out[i+8:i+16]  = palette_rgba[j+16:j+24]
        out[i+24:i+32] = palette_rgba[j+24:j+32]
        j += 32
    return out

def _unswizzle8_v2(width: int, height: int, data: bytes) -> bytes:
    out = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            page_x = x & ~0x7f
            page_y = y & ~0x7f
            pages_horz = (width + 127) // 128
            pages_vert = (height + 127) // 128
            page_number = (page_y // 128) * pages_horz + (page_x // 128)
            page32y = (page_number // pages_vert) * 32
            page32x = (page_number % pages_vert) * 64
            page_location = page32y * height * 2 + page32x * 4
            loc_x = x & 0x7f
            loc_y = y & 0x7f
            block_location = ((loc_x & ~0x1f) >> 1) * height + (loc_y & ~0xf) * 2
            swap_selector = (((y + 2) >> 2) & 1) * 4
            pos_y = (((y & ~3) >> 1) + (y & 1)) & 7
            column_location = pos_y * height * 2 + ((x + swap_selector) & 7) * 4
            byte_num = (x >> 3) & 3
            src = page_location + block_location + column_location + byte_num
            if src < len(data):
                out[y * width + x] = data[src]
    return bytes(out)

def parse_texture_array(data: bytes) -> list:
    if len(data) < 0x10:
        return []

    off = 0
    texture_count  = struct.unpack_from('<I', data, off)[0]; off += 4
    pointers_count = struct.unpack_from('<I', data, off)[0]; off += 4
    off += 8  # padding

    _dbg(f"  Texturas: texture_count={texture_count} pointers_count={pointers_count}")

    if texture_count == 0 or pointers_count == 0:
        return []

    # DadosTipo1: texture_count × 0x50 (ignorado)
    off += texture_count * 0x50

    # DadosTipo2: texture_count × 0x14
    tex_entries = []
    for _ in range(texture_count):
        if off + 0x14 > len(data): break
        te = TextureEntry()
        te.pixel_index   = struct.unpack_from('<H', data, off + 0x00)[0]
        te.palette_index = struct.unpack_from('<H', data, off + 0x08)[0]
        te.width         = struct.unpack_from('<H', data, off + 0x10)[0]
        te.height        = struct.unpack_from('<H', data, off + 0x12)[0]
        tex_entries.append(te)
        off += 0x14

    # PointerRows: pointers_count × 0x10
    # IMPORTANTE: os offsets dos Ptrs são relativos ao início desta tabela (ptr_start)
    ptr_start = off
    ptr_entries = []
    for tc in range(pointers_count):
        if off + 0x10 > len(data): break
        pe      = PointerEntry()
        raw_off = struct.unpack_from('<I', data, off + 0x04)[0]
        pe.offset  = raw_off + 0x10 * tc
        pe.width   = struct.unpack_from('<H', data, off + 0x08)[0]
        pe.height  = struct.unpack_from('<H', data, off + 0x0A)[0]
        pe.height1 = struct.unpack_from('<H', data, off + 0x0C)[0]
        ptr_entries.append(pe)
        off += 0x10

    data_start = ptr_start  # offsets dos Ptrs são relativos ao início da tabela de ponteiros

    for tc in range(len(ptr_entries)):
        if tc == len(ptr_entries) - 1:
            sz = len(data) - data_start - ptr_entries[tc].offset
        else:
            sz = ptr_entries[tc + 1].offset - ptr_entries[tc].offset

        if tc == len(ptr_entries) - 1 and 900 <= sz < 1024:
            sz = min(sz, len(data) - data_start - ptr_entries[tc].offset)

        ptr_entries[tc].size = sz

        ptr_entries[tc].bpp = 8
        if sz <= 1024:
            ptr_entries[tc].bpp = 8
        if sz <= 64:
            ptr_entries[tc].bpp = 4
        if sz > 1024:
            ptr_entries[tc].bpp = 0  # TEX


    entries_data = []
    for pe in ptr_entries:
        start = data_start + pe.offset
        end   = start + pe.size
        entries_data.append(data[start:end] if end <= len(data) else b'')

    for te in tex_entries:
        if te.palette_index < len(ptr_entries):
            te.bpp = ptr_entries[te.palette_index].bpp
            if te.bpp == 0:
                te.bpp = 8
    # bpp será reconfirmado pelo tamanho real da paleta lida (igual ao C# GetImage)

    images = []
    for t_idx, te in enumerate(tex_entries):
        if te.pixel_index >= len(entries_data) or te.palette_index >= len(entries_data):
            continue
        if te.width == 0 or te.height == 0:
            continue


        pixel_raw   = entries_data[te.pixel_index]
        palette_raw = entries_data[te.palette_index]

        palette_rgba = []
        pos = 0
        while pos + 3 < len(palette_raw):
            r = palette_raw[pos]
            g = palette_raw[pos+1]
            b = palette_raw[pos+2]
            a = palette_raw[pos+3]
            if a <= 128:                          # igual ao C#: só escala se <= 128
                a = (a * 255) // 128
            palette_rgba.append((r, g, b, a))
            pos += 4

        # C# decide o path pelo tamanho real da paleta lida:
        #   cores.Length <= 256 && > 64  -> 8bpp
        #   cores.Length <= 16           -> 4bpp
        pal_len = len(palette_rgba)
        if pal_len > 64:
            actual_bpp = 8
            while len(palette_rgba) < 256:
                palette_rgba.append((0, 0, 0, 255))
            if len(palette_rgba) == 256:
                palette_rgba = _unswizzle_palette(palette_rgba)
        elif pal_len <= 16:
            actual_bpp = 4
        else:
            actual_bpp = te.bpp  # fallback

        if actual_bpp == 8:
            expected = te.width * te.height
            if len(pixel_raw) < expected:
                continue
            pixels = unswizzle8(te.width, te.height, pixel_raw)
        else:
            # 4bpp: usa ea_swizzle.dll igual ao C#
            pixels = _ea_unswizzle4(pixel_raw, te.width, te.height)

        img_name = f"tex_{t_idx}"
        img = bpy.data.images.new(img_name, width=te.width, height=te.height, alpha=True)
        img.alpha_mode = 'STRAIGHT'

        pixel_count = te.width * te.height
        rgba_flat = []

        if actual_bpp == 8:
            for i in range(pixel_count):
                idx = pixels[i] if i < len(pixels) else 0
                r, g, b, a = palette_rgba[idx] if idx < len(palette_rgba) else (0, 0, 0, 255)
                rgba_flat.extend([r/255.0, g/255.0, b/255.0, a/255.0])
        else:
            for i in range(pixel_count):
                byte_idx = i // 2
                if byte_idx < len(pixels):
                    nibble = (pixels[byte_idx] & 0xF) if (i & 1) == 0 else (pixels[byte_idx] >> 4)
                else:
                    nibble = 0
                r, g, b, a = palette_rgba[nibble] if nibble < len(palette_rgba) else (0, 0, 0, 255)
                rgba_flat.extend([r/255.0, g/255.0, b/255.0, a/255.0])

        # Blender usa origem no canto inferior esquerdo: só flip vertical, sem flip horizontal
        row_size = te.width * 4
        rows = [rgba_flat[i*row_size:(i+1)*row_size] for i in range(te.height)]
        rows = rows[::-1]
        flat = [v for row in rows for v in row]

        img.pixels[:] = flat
        img.pack()
        img.update()
        images.append(img)

    return images


# ── read_mesh_texture_index ───────────────────────────────────────────────────

def _read_mesh_texture_index(data: bytes, mesh_material_data_offset: int) -> int:
    """
    Lê o textureIndex do mesh_material_data_offset.
    Estrutura: int32 textureIndex, depois 0xC bytes ignorados (total 0x10 por entrada).
    Retorna o índice da textura, ou -1 se inválido.
    """
    if mesh_material_data_offset == 0:
        return -1
    off = mesh_material_data_offset
    if off + 4 > len(data):
        return -1
    texture_index = struct.unpack_from('<i', data, off)[0]
    return texture_index


# ── _make_material ────────────────────────────────────────────────────────────

def _make_material(mat_name: str, image: bpy.types.Image) -> bpy.types.Material:
    """
    Cria (ou reutiliza) um material com nó Image Texture ligado ao BSDF.
    Usa alpha BLEND se a imagem tiver canal alpha.
    """
    # Reutiliza material existente com mesmo nome
    mat = bpy.data.materials.get(mat_name)
    if mat is not None:
        return mat

    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    mat.blend_method = 'CLIP'

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    # Output
    node_out = nodes.new('ShaderNodeOutputMaterial')
    node_out.location = (400, 0)

    # Principled BSDF
    node_bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    node_bsdf.location = (0, 0)

    # Image Texture
    node_tex = nodes.new('ShaderNodeTexImage')
    node_tex.location = (-400, 0)
    node_tex.image = image
    node_tex.interpolation = 'Closest'  # pixel art / PS2

    # UV Map
    node_uv = nodes.new('ShaderNodeUVMap')
    node_uv.location = (-700, 0)
    node_uv.uv_map = "UVMap"

    links.new(node_uv.outputs['UV'],        node_tex.inputs['Vector'])
    links.new(node_tex.outputs['Color'],    node_bsdf.inputs['Base Color'])
    links.new(node_tex.outputs['Alpha'],    node_bsdf.inputs['Alpha'])
    links.new(node_bsdf.outputs['BSDF'],   node_out.inputs['Surface'])

    # Sem metallic/roughness excessivos
    node_bsdf.inputs['Metallic'].default_value  = 0.0
    node_bsdf.inputs['Roughness'].default_value = 1.0

    return mat


# ── _apply_material_to_object ─────────────────────────────────────────────────

def _apply_material_to_object(obj: bpy.types.Object,
                               mat: bpy.types.Material):
    """Garante que o material está no slot 0 do objeto."""
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


# ── MdlMesh ───────────────────────────────────────────────────────────────────

class MdlMesh:
    __slots__ = (
        'bone_palette_offset', 'mesh_data_offset', 'unk2_offset', 'unk3_offset',
        'unk_flag1', 'unk_flag2', 'mesh_material_data_offset', 'mesh_color',
        'vertex_type', 'vertex_type_uses', 'init_found', 'current_model',
        'uv_skip', 'is_child_model', 'init_index', 'last_main_model',
        'current_group', 'vertex_list', 'vertex_normals_list', 'uvs_list',
        'faces_indices_list', 'normals_indices_list', 'uvs_indices_list',
        'model_group', 'teste',
        'bone_offsets',
        'bone_offsets_ordered',
        '_current_skin_bone_id',
        '_next4_count',
        '_bone_palette',
        '_bone_flat_list',
        '_bone_flip_flags',
        '_uv_chunk_base',   # offset base de UV no momento do último chunk de UVs
    )

    def __init__(self):
        self.bone_palette_offset               = 0
        self.mesh_data_offset          = 0
        self.unk2_offset               = 0
        self.unk3_offset               = 0
        self.unk_flag1                 = 0
        self.unk_flag2                 = 0
        self.mesh_material_data_offset = 0
        self.mesh_color                = 0
        self._reset_model_state()

    def _reset_model_state(self):
        self.vertex_type      = 0
        self.vertex_type_uses = 0
        self.init_found       = False
        self.current_model    = 0
        self.uv_skip          = True
        self.is_child_model   = False
        self.init_index       = 0
        self.last_main_model  = -1
        self.current_group    = -1
        self.vertex_list          = []
        self.vertex_normals_list  = []
        self.uvs_list             = []
        self.faces_indices_list   = []
        self.normals_indices_list = []
        self.uvs_indices_list     = []
        self.model_group          = []
        self.teste                = []
        self.bone_offsets         = {}
        self.bone_offsets_ordered = []
        self._current_skin_bone_id = -1
        self._next4_count          = 0
        self._bone_palette         = []
        self._bone_flat_list       = []
        self._bone_flip_flags      = []
        self._uv_chunk_base        = {}  # {model_idx: base_uv_count} por sub-modelo

    def _new_model_slot(self):
        self.vertex_list.append([])
        self.vertex_normals_list.append([])
        self.uvs_list.append([])
        self.faces_indices_list.append([])
        self.normals_indices_list.append([])
        self.uvs_indices_list.append([])
        self.model_group.append(self.current_group)


# ── Bone ─────────────────────────────────────────────────────────────────────

class Bone:
    __slots__ = ('rotation', 'scale', 'unk', 'position', 'parent_index')

    def __init__(self):
        self.rotation     = (0.0, 0.0, 0.0, 1.0)
        self.scale        = (1.0, 1.0, 1.0, 1.0)
        self.unk          = (0.0, 0.0, 0.0, 1.0)
        self.position     = (0.0, 0.0, 0.0, 1.0)
        self.parent_index = -1


# ── RawMdl ────────────────────────────────────────────────────────────────────

class RawMdl:
    def __init__(self):
        self.meshes: list = []
        self.bones:  list = []
        self._raw_data: bytes = b''

    def parse(self, raw_file: RawFile):
        data = raw_file.data
        self._raw_data = data
        self.meshes.clear()
        self.bones.clear()

        if len(data) < 8:
            return

        offset = 0
        mesh_table_offset = struct.unpack_from('<I', data, offset)[0]; offset += 4
        mesh_count        = struct.unpack_from('<I', data, offset)[0]; offset += 4
        _dbg(f"meshTableOffset={mesh_table_offset:#010x}, meshCount={mesh_count}")

        bone_count = struct.unpack_from('<I', data, offset)[0]; offset += 4
        unk2       = struct.unpack_from('<I', data, offset)[0]; offset += 4
        _dbg(f"boneCount={bone_count}, unk2={unk2}")

        for b in range(bone_count):
            bone = Bone()
            bone.rotation = struct.unpack_from('<4f', data, offset); offset += 16
            bone.scale    = struct.unpack_from('<4f', data, offset); offset += 16
            bone.unk      = struct.unpack_from('<4f', data, offset); offset += 16
            bone.position = struct.unpack_from('<4f', data, offset); offset += 16
            self.bones.append(bone)
            _dbg(f"  Bone {b}: pos={bone.position[:3]}  rot={bone.rotation}")

        for b_idx, bone in enumerate(self.bones):

            pass
        for b, bone in enumerate(self.bones):
            if offset + 2 > len(data):
                break
            bone.parent_index = struct.unpack_from('<h', data, offset)[0]; offset += 2

        t = mesh_table_offset
        for i in range(mesh_count):
            if t + 32 > len(data):
                break
            m = MdlMesh()
            m.bone_palette_offset               = struct.unpack_from('<I', data, t)[0]; t += 4
            m.mesh_data_offset          = struct.unpack_from('<I', data, t)[0]; t += 4
            m.unk2_offset               = struct.unpack_from('<I', data, t)[0]; t += 4
            m.unk3_offset               = struct.unpack_from('<I', data, t)[0]; t += 4
            m.unk_flag1                 = struct.unpack_from('<I', data, t)[0]; t += 4
            m.unk_flag2                 = struct.unpack_from('<I', data, t)[0]; t += 4
            m.mesh_material_data_offset = struct.unpack_from('<I', data, t)[0]; t += 4
            m.mesh_color                = struct.unpack_from('<I', data, t)[0]; t += 4
            self.meshes.append(m)
            _dbg(f"  MdlMesh {i}: bone_palette={m.bone_palette_offset:#x} meshData={m.mesh_data_offset:#x} "
                 f"materialData={m.mesh_material_data_offset:#x}")

        sorted_offsets = sorted(m.mesh_data_offset for m in self.meshes)

        def mesh_end(mesh_data_offset):
            for off in sorted_offsets:
                if off > mesh_data_offset:
                    return off
            return len(data)

        for mesh_idx, mesh in enumerate(self.meshes):
            chunk_start = mesh.mesh_data_offset + 0xC0
            chunk_end   = mesh_end(mesh.mesh_data_offset)
            if chunk_start >= len(data):
                continue
            _dbg(f"\n  --- MdlMesh {mesh_idx} @ [{chunk_start:#x}, {chunk_end:#x}) ---")
            mesh._reset_model_state()
            _parse_mesh_chunks(data, chunk_start, chunk_end, mesh)

            _dbg(f"  MdlMesh {mesh_idx}: {mesh.current_model} sub-modelos, "
                 f"{len(mesh.bone_offsets)} skin_bones no chunk")

            _dbg(f"  Lendo bone_palette em {mesh.bone_palette_offset:#x}:")
            mesh._bone_flat_list, mesh._bone_palette, mesh._bone_flip_flags = parse_bone_palette(data, mesh.bone_palette_offset)

        _dbg(f"\n--- Fim: {len(self.meshes)} mesh(es), {len(self.bones)} bone(s) ---")


# ── parser de chunks ──────────────────────────────────────────────────────────

def _parse_mesh_chunks(data: bytes, start: int, end: int, mesh: MdlMesh):
    off = start
    while off + 4 <= end:
        buf4 = data[off:off + 4]
        off += 4
        off = _read_chunk(data, off, buf4, mesh)


def _read_chunk(data: bytes, offset: int, buf4: bytes, mesh: MdlMesh) -> int:
    if struct.unpack_from('<I', buf4)[0] == 0x6E01C000:
        mesh.init_found = True
    if not mesh.init_found:
        return offset
    t = buf4[3]
    if   t == 0x62: offset = _chunk_list_int8(data, offset, buf4, mesh)
    elif t == 0x66: offset = _chunk_list_int16(data, offset, buf4, mesh)
    elif t == 0x68: offset = _chunk_uvs(data, offset, buf4, mesh)
    elif t == 0x6A: offset = _chunk_indexes(data, offset, buf4, mesh)
    elif t == 0x6C: offset = _chunk_vertex_type(data, offset, buf4, mesh)
    elif t == 0x6E: offset = _chunk_init_model(data, offset, buf4, mesh)
    return offset


def _chunk_init_model(data, offset, buf4, mesh):
    if offset + 4 > len(data): return offset
    next4 = data[offset:offset+4]; offset += 4

    if next4[0] == 0x04 and next4[1] == 0x10:
        if offset + 4 > len(data): return offset
        next8 = data[offset:offset+4]; offset += 4
        if next8[0] == 0x2E:
            mesh.last_main_model = len(mesh.vertex_list) - 1
            mesh.is_child_model  = False
            mesh.uv_skip         = True
            mesh.current_group  += 1
    else:
        if offset + 4 <= len(data):
            peek = data[offset:offset+4]
            if peek[3] == 0x6C and peek[1] == 0x80:
                skin_bone_id           = next4[1]
                mesh._current_skin_bone_id = skin_bone_id
                mesh._next4_count          = next4[0]
    return offset


def _chunk_list_int16(data, offset, buf4, mesh):
    return offset + buf4[0] * 2


def _chunk_list_int8(data, offset, buf4, mesh):
    mesh.teste = [[]]
    unk = buf4[0]; length = buf4[2]
    for j in range(length):
        if offset >= len(data): break
        b = data[offset]; offset += 1
        if j == 0: mesh.init_index = b
        mesh.teste[0].append(b)
    offset = align4(offset)
    mesh.vertex_type      = unk
    mesh.vertex_type_uses = buf4[1]
    return offset


def _chunk_vertex_type(data, offset, buf4, mesh):
    if mesh._current_skin_bone_id >= 0:
        skin_bone_id               = mesh._current_skin_bone_id
        mesh._current_skin_bone_id = -1
        mesh._next4_count          = 0
        mesh.vertex_type_uses      = 0

        count     = buf4[2]
        model_idx = max(0, mesh.current_model - 1)

        entries = mesh.bone_offsets.setdefault(skin_bone_id, [])
        ordered_entries = []
        mesh.bone_offsets_ordered.append((skin_bone_id, ordered_entries))
        for _ in range(count):
            if offset + 16 > len(data): break
            x = struct.unpack_from('<f', data, offset)[0]
            y = struct.unpack_from('<f', data, offset + 4)[0]
            z = struct.unpack_from('<f', data, offset + 8)[0]
            vert_idx = struct.unpack_from('<I', data, offset + 12)[0]
            offset += 16
            entries.append((model_idx, vert_idx, x, y, z))
            ordered_entries.append((model_idx, vert_idx, x, y, z))
        return offset

    if mesh.vertex_type_uses <= 0:
        return offset
    mesh.vertex_type_uses -= 1
    vt = mesh.vertex_type
    if   vt == 1: offset = _read_normals(data, offset, buf4, mesh)
    elif vt == 2: offset = _read_vertices(data, offset, buf4, mesh)
    return offset


def _read_vertices(data, offset, buf4, mesh):
    primeiro_chunk = mesh.uv_skip
    if mesh.uv_skip:
        mesh._new_model_slot()
        mesh.current_model += 1
    m = mesh.current_model - 1

    if len(mesh.vertex_list[m]) == 0:
        slot_count = mesh.teste[0][0] + 1
        mesh.vertex_list[m] = [[] for _ in range(slot_count)]

    for i in range(len(mesh.teste[0])):
        if offset + 0x10 > len(data): break
        x = struct.unpack_from('<f', data, offset)[0]
        y = struct.unpack_from('<f', data, offset+4)[0]
        z = struct.unpack_from('<f', data, offset+8)[0]
        offset += 0x10
        idx = mesh.teste[0][i]
        while len(mesh.vertex_list[m]) <= idx:
            mesh.vertex_list[m].append([])
        mesh.vertex_list[m][idx] = [x, y, z]

    if mesh.is_child_model and primeiro_chunk:
        lm = mesh.last_main_model
        main = mesh.vertex_list[lm] if 0 <= lm < len(mesh.vertex_list) else []
        while len(mesh.vertex_list[m]) < len(main):
            mesh.vertex_list[m].append([])
        for i in range(len(mesh.vertex_list[m])):
            if len(mesh.vertex_list[m][i]) == 0 and i < len(main):
                src = main[i]
                mesh.vertex_list[m][i] = src if _is_ref(src) else (_REF, lm, i)

        # Filho herda UVs do pai — índices UV do filho já são absolutos
        # referenciando a lista completa (pai + próprias do filho)
        if 0 <= lm < len(mesh.uvs_list) and not mesh.uvs_list[m]:
            mesh.uvs_list[m] = list(mesh.uvs_list[lm])
            # Reseta o base para 0: os uv_idx do filho são absolutos
            mesh._uv_chunk_base[m] = 0

    if mesh.vertex_list[m]:
        mesh.uv_skip = False
    return offset


def _read_normals(data, offset, buf4, mesh):
    count = buf4[2]; m = mesh.current_model - 1
    if m < 0 or m >= len(mesh.vertex_normals_list):
        return offset + count * 0x10
    for _ in range(count):
        if offset + 0x10 > len(data): break
        nx = struct.unpack_from('<f', data, offset)[0]
        ny = struct.unpack_from('<f', data, offset+4)[0]
        nz = struct.unpack_from('<f', data, offset+8)[0]
        offset += 0x10
        mesh.vertex_normals_list[m].insert(0, nz)
        mesh.vertex_normals_list[m].insert(0, ny)
        mesh.vertex_normals_list[m].insert(0, nx)
    return offset


def _chunk_uvs(data, offset, buf4, mesh):
    mesh.vertex_type_uses = 0
    count = buf4[2]; m = mesh.current_model - 1
    if m < 0 or m >= len(mesh.uvs_list):
        return offset + count * 12
    # Para sub-modelos que NÃO são filhos: registra o base antes de adicionar UVs
    # Para filhos: os índices já são absolutos (referenciam pai+filho), base=0
    if not mesh.is_child_model:
        mesh._uv_chunk_base[m] = len(mesh.uvs_list[m]) // 2
    else:
        mesh._uv_chunk_base[m] = 0
    for _ in range(count):
        if offset + 12 > len(data): break
        u = struct.unpack_from('<f', data, offset)[0]
        v = struct.unpack_from('<f', data, offset+4)[0]
        offset += 12
        mesh.uvs_list[m].append(u)
        mesh.uvs_list[m].append(v)
    return offset


def _chunk_indexes(data, offset, buf4, mesh):
    mesh.vertex_type_uses = 0
    mesh.is_child_model   = True
    mesh.uv_skip          = True
    m = mesh.current_model - 1; count = buf4[2]
    if m < 0 or m >= len(mesh.faces_indices_list):
        return align4(offset + count * 9)
    total = count * 3; j = 0; tris = []
    while j < total:
        if j == 0: offset += 3
        else:
            if offset + 3 > len(data): break
            f = data[offset]; n = data[offset+1]; uv = data[offset+2]; offset += 3
            tris.append((f, n, uv))
        j += 3
    offset = align4(offset)

    uv_base = mesh._uv_chunk_base.get(m, 0)
    raw_uvs = [t[2] for t in tris]

    mesh.faces_indices_list[m]   += [t[0] for t in tris]
    mesh.normals_indices_list[m] += [t[1] for t in tris]
    mesh.uvs_indices_list[m]     += [t[2] + uv_base for t in tris]
    mesh.last_main_model = m
    return offset


# ── helpers de geometria ──────────────────────────────────────────────────────

def _slot_xyz(slot):
    if isinstance(slot, list) and len(slot) >= 3:
        return slot[0], slot[1], slot[2]
    return None


def _make_indices_global_mesh(mesh: MdlMesh):
    for i in range(len(mesh.vertex_list)):
        fi = mesh.faces_indices_list[i] if i < len(mesh.faces_indices_list) else []
        if fi:
            needed = max(fi) + 1
            if len(mesh.vertex_list[i]) > needed:
                mesh.vertex_list[i] = mesh.vertex_list[i][:needed]
    f = 0
    for i in range(len(mesh.vertex_list)):
        if i > 0:
            for j in range(len(mesh.faces_indices_list[i])):
                mesh.faces_indices_list[i][j] += f
        f += len(mesh.vertex_list[i])


def _get_groups(mesh: MdlMesh):
    groups = {}
    for i, gid in enumerate(mesh.model_group):
        if gid >= 0:
            groups.setdefault(gid, []).append(i)
    return groups


# ── bone world matrix ─────────────────────────────────────────────────────────

def _bone_local_matrix(bone) -> Matrix:
    qx, qy, qz, qw = bone.rotation
    sx, sy, sz = bone.scale[0], bone.scale[1], bone.scale[2]
    local_rot   = Quaternion((qw, qx, qy, qz)).to_matrix().to_4x4()
    local_scale = Matrix.Diagonal((sx, sy, sz, 1.0))
    local_trans = Matrix.Translation(bone.position[:3])
    return local_trans @ local_rot @ local_scale


def _bone_world_matrix(bones: list, bone_idx: int) -> Matrix:
    bone = bones[bone_idx]
    local_mat = _bone_local_matrix(bone)
    if bone.parent_index < 0:
        return local_mat
    return _bone_world_matrix(bones, bone.parent_index) @ local_mat


# ── skin_bone_id → joint_bone_index via paleta unk1 ──────────────────────────

def _skin_to_joint(skin_bone_id: int, bone_palette: list, joint_bone_count: int) -> int:
    if joint_bone_count <= 0:
        return 0
    if joint_bone_count == 1:
        return 0
    if bone_palette:
        try:
            return bone_palette.index(skin_bone_id) % joint_bone_count
        except ValueError:
            pass
    return skin_bone_id % joint_bone_count


# ── vertex groups ─────────────────────────────────────────────────────────────

def _apply_vertex_groups(obj, mesh: MdlMesh, slot_to_bm: dict,
                         all_members: list, joint_bone_count: int):
    flat_list = mesh._bone_flat_list

    if not flat_list:
        vg = obj.vertex_groups.new(name="bone_00")
        all_bm = list(set(v for v in slot_to_bm.values() if v is not None))
        if all_bm:
            vg.add(all_bm, 1.0, 'REPLACE')
        return

    for model_idx in all_members:
        entry_idx    = min(model_idx, len(flat_list) - 1)
        joint_idx = flat_list[entry_idx]
        if joint_bone_count > 0:
            joint_idx = joint_idx % joint_bone_count

        bone_name = f"bone_{joint_idx:02d}"
        vg = obj.vertex_groups.get(bone_name)
        if vg is None:
            vg = obj.vertex_groups.new(name=bone_name)

        bm_indices = []
        if model_idx < len(mesh.vertex_list):
            for si in range(len(mesh.vertex_list[model_idx])):
                bm_idx = slot_to_bm.get((model_idx, si))
                if bm_idx is not None:
                    bm_indices.append(bm_idx)

        if bm_indices:
            vg.add(list(set(bm_indices)), 1.0, 'REPLACE')


def _build_mesh_object(context, mesh: MdlMesh, obj_name: str,
                       scale: float, flip_x: bool, flip_y: bool,
                       joint_bone_count: int, bones: list = None):
    groups = _get_groups(mesh)
    if not groups:
        groups = {0: list(range(len(mesh.vertex_list)))}

    vert_offsets = []
    acc = 0
    for i in range(len(mesh.vertex_list)):
        vert_offsets.append(acc)
        acc += len(mesh.vertex_list[i])

    all_members = [mi for members in
                   [groups[g] for g in sorted(groups)] for mi in members]

    n_faces = sum(len(mesh.faces_indices_list[mi]) for mi in all_members
                  if mi < len(mesh.faces_indices_list))
    if n_faces == 0:
        return None

    group_verts = []
    slot_to_bm  = {}

    for mi in all_members:
        if mi >= len(mesh.vertex_list): continue

        entry_idx = min(mi, len(mesh._bone_flat_list) - 1) if mesh._bone_flat_list else -1
        joint_idx = mesh._bone_flat_list[entry_idx] if entry_idx >= 0 else 0
        if joint_bone_count > 0:
            joint_idx = joint_idx % joint_bone_count

        if bones and joint_idx < len(bones):
            bone_mat = _bone_world_matrix(bones, joint_idx)
        else:
            bone_mat = Matrix.Identity(4)

        for si, slot in enumerate(mesh.vertex_list[mi]):
            if not _is_ref(slot):
                xyz = _slot_xyz(slot)
                if xyz is not None:
                    x, y, z = xyz
                    v_world = bone_mat @ Vector((x, y, z))
                    x, y, z = v_world.x, v_world.y, v_world.z
                    if flip_x: x = -x
                    if flip_y: y = -y
                    slot_to_bm[(mi, si)] = len(group_verts)
                    group_verts.append(Vector((x*scale, y*scale, z*scale)))
                else:
                    slot_to_bm[(mi, si)] = None

    for mi in all_members:
        if mi >= len(mesh.vertex_list): continue

        entry_idx = min(mi, len(mesh._bone_flat_list) - 1) if mesh._bone_flat_list else -1
        joint_idx = mesh._bone_flat_list[entry_idx] if entry_idx >= 0 else 0
        if joint_bone_count > 0:
            joint_idx = joint_idx % joint_bone_count

        if bones and joint_idx < len(bones):
            bone_mat = _bone_world_matrix(bones, joint_idx)
        else:
            bone_mat = Matrix.Identity(4)

        for si, slot in enumerate(mesh.vertex_list[mi]):
            if not _is_ref(slot): continue
            rm, rs = _resolve_ref(mesh.vertex_list, slot[1], slot[2])
            if rm is None:
                slot_to_bm[(mi, si)] = None
                continue
            resolved = slot_to_bm.get((rm, rs))
            if resolved is not None:
                slot_to_bm[(mi, si)] = resolved
            else:
                vl = mesh.vertex_list[rm]
                if rs < len(vl):
                    xyz = _slot_xyz(vl[rs])
                    if xyz is not None:
                        x, y, z = xyz
                        v_world = bone_mat @ Vector((x, y, z))
                        x, y, z = v_world.x, v_world.y, v_world.z
                        if flip_x: x = -x
                        if flip_y: y = -y
                        slot_to_bm[(mi, si)] = len(group_verts)
                        slot_to_bm[(rm, rs)] = len(group_verts)
                        group_verts.append(Vector((x*scale, y*scale, z*scale)))
                    else:
                        slot_to_bm[(mi, si)] = None
                else:
                    slot_to_bm[(mi, si)] = None

    if not group_verts:
        return None

    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")
    bm_verts = [bm.verts.new(v) for v in group_verts]
    bm.verts.ensure_lookup_table(); bm.verts.index_update()

    faces_ok = faces_skip = 0
    for mi in all_members:
        if mi >= len(mesh.faces_indices_list): continue
        fi  = mesh.faces_indices_list[mi]
        ui  = mesh.uvs_indices_list[mi]
        uvs = mesh.uvs_list[mi]
        v_off = vert_offsets[mi]
        j = 0
        while j + 2 < len(fi):
            gi_a, gi_b, gi_c = fi[j], fi[j+1], fi[j+2]
            la = slot_to_bm.get((mi, gi_a - v_off))
            lb = slot_to_bm.get((mi, gi_b - v_off))
            lc = slot_to_bm.get((mi, gi_c - v_off))
            if la is None or lb is None or lc is None:
                faces_skip += 1
            elif la < len(bm_verts) and lb < len(bm_verts) and lc < len(bm_verts):
                try:
                    face = bm.faces.new((bm_verts[la], bm_verts[lb], bm_verts[lc]))
                    face.smooth = True

                    if j + 2 < len(ui):
                        uv_a = ui[j]
                        uv_b = ui[j+1]
                        uv_c = ui[j+2]
                        for loop, uv_idx in zip(face.loops, (uv_a, uv_b, uv_c)):
                            base = uv_idx * 2
                            if base + 1 < len(uvs):
                                loop[uv_layer].uv = (uvs[base], 1.0 - uvs[base + 1])

                    faces_ok += 1
                except ValueError:
                    pass
            else:
                faces_skip += 1
            j += 3


    mesh_data = bpy.data.meshes.new(obj_name)
    obj       = bpy.data.objects.new(obj_name, mesh_data)
    context.collection.objects.link(obj)
    bm.to_mesh(mesh_data); bm.free()

    for mi in all_members:
        if mi >= len(mesh.vertex_normals_list): continue
        nr = mesh.vertex_normals_list[mi]
        if not nr: continue
        normals_out = []
        k = 0
        while k + 2 < len(nr):
            nx, ny, nz = nr[k], nr[k+1], nr[k+2]
            if bones and joint_idx < len(bones):
                n_world = _bone_local_matrix(bones[joint_idx]).to_3x3() @ Vector((nx, ny, nz))
                nx, ny, nz = n_world.x, n_world.y, n_world.z
            if flip_x: nx = -nx
            if flip_y: ny = -ny
            normals_out.append(Vector((nx, ny, nz)).normalized())
            k += 3
        if len(normals_out) == len(mesh_data.vertices):
            mesh_data.normals_split_custom_set_from_vertices(normals_out)
            break

    mesh_data.update()

    _apply_vertex_groups(obj, mesh, slot_to_bm, all_members, joint_bone_count)

    return obj


# ── build_armature ────────────────────────────────────────────────────────────

def build_armature(context, mdl: RawMdl, base_name: str, scale: float,
                   flip_x: bool, flip_y: bool):
    if not mdl.bones:
        return None

    arm_data = bpy.data.armatures.new(base_name + "_armature")
    arm_data.display_type = 'STICK'
    arm_obj  = bpy.data.objects.new(base_name + "_armature", arm_data)
    context.collection.objects.link(arm_obj)

    context.view_layer.objects.active = arm_obj
    arm_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = arm_data.edit_bones
    created    = []

    bone0_mat = _bone_world_matrix(mdl.bones, 0)
    bone0_inv = bone0_mat.inverted()

    # Rotação do bone 0 para aplicar no objeto armadura
    bone0_rot = bone0_mat.to_euler()

    for b_idx, bone in enumerate(mdl.bones):
        eb = edit_bones.new(f"bone_{b_idx:02d}")
        eb.use_connect = False

        world_mat = _bone_world_matrix(mdl.bones, b_idx)
        relative_mat = bone0_inv @ world_mat
        hw = relative_mat.translation

        hx = -hw.x if flip_x else hw.x
        hy = -hw.y if flip_y else hw.y

        eb.head = Vector((hx * scale, hy * scale, hw.z * scale))
        eb.tail = eb.head + Vector((0, 0.05 * scale, 0))

        created.append(eb.name)

    for b_idx, bone in enumerate(mdl.bones):
        if 0 <= bone.parent_index < len(created):
            edit_bones[created[b_idx]].parent = edit_bones[created[bone.parent_index]]

    bpy.ops.object.mode_set(mode='OBJECT')

    # Usa a matriz de rotação do bone 0 diretamente no objeto armadura
    bone0_rot_mat = bone0_mat.to_3x3().normalized().to_4x4()
    arm_obj.matrix_world = bone0_rot_mat

    _dbg(f"Armature: {len(mdl.bones)} bones")
    return arm_obj

def _bone_world_position(bones: list, bone_idx: int) -> tuple:
    """Acumula só as posições dos bones pai, sem aplicar rotação."""
    bone = bones[bone_idx]
    px, py, pz = bone.position[0], bone.position[1], bone.position[2]
    if bone.parent_index >= 0:
        ppx, ppy, ppz = _bone_world_position(bones, bone.parent_index)
        px += ppx; py += ppy; pz += ppz
    return px, py, pz

# ── build_mesh ────────────────────────────────────────────────────────────────

def build_mesh(context, mdl: RawMdl, base_name: str,
               scale: float, flip_x: bool, flip_y: bool,
               merge_objects: bool, textures: list = None):
    if not mdl.meshes:
        return {'CANCELLED'}

    joint_bone_count = len(mdl.bones)
    created_objects  = []
    raw_data         = mdl._raw_data

    for mesh_idx, mesh in enumerate(mdl.meshes):
        _make_indices_global_mesh(mesh)
        obj_name = f"{base_name}_{mesh_idx}"
        obj = _build_mesh_object(
            context, mesh, obj_name,
            scale, flip_x, flip_y, joint_bone_count,
            bones=mdl.bones)

        if obj is not None:
            # ── Lê textureIndex e aplica material ──────────────────────────
            tex_idx = _read_mesh_texture_index(raw_data, mesh.mesh_material_data_offset)

            if textures and 0 <= tex_idx < len(textures):
                image    = textures[tex_idx]
                mat_name = f"mat_tex{tex_idx}"
                mat      = _make_material(mat_name, image)
                _apply_material_to_object(obj, mat)
            elif tex_idx >= 0:
                pass

            created_objects.append(obj)

    arm_obj = build_armature(context, mdl, base_name, scale, flip_x, flip_y)

    if arm_obj is not None:
        for obj in created_objects:
            obj.parent = arm_obj
            obj.parent_type = 'OBJECT'
            obj.matrix_parent_inverse = Matrix.Identity(4)
            mod = obj.modifiers.new(name="Armature", type='ARMATURE')
            mod.object = arm_obj

    all_created = created_objects + ([arm_obj] if arm_obj else [])
    if all_created:
        for o in context.selected_objects:
            o.select_set(False)
        for o in all_created:
            o.select_set(True)
        context.view_layer.objects.active = arm_obj if arm_obj else created_objects[0]

    return {'FINISHED'}


# ── entry point ───────────────────────────────────────────────────────────────

def _clear_scene(context):
    """Remove todos os objetos, meshes, materiais e texturas da cena."""
    # Desseleciona tudo e remove objetos
    for obj in list(context.scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    # Remove meshes órfãs
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh, do_unlink=True)

    # Remove armatures órfãs
    for arm in list(bpy.data.armatures):
        bpy.data.armatures.remove(arm, do_unlink=True)

    # Remove materiais órfãos
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat, do_unlink=True)

    # Remove texturas/imagens órfãs
    for img in list(bpy.data.images):
        bpy.data.images.remove(img, do_unlink=True)

    _dbg("  Cena limpa: objetos, meshes, armatures, materiais e texturas removidos.")


def _setup_viewport(context):
    """Ativa Flat (Solid + MatCap desligado) e Material Preview (Texture) em todos os viewports 3D."""
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            for space in area.spaces:
                if space.type == 'VIEW_3D':
                    # Material Preview = textura visível
                    space.shading.type = 'MATERIAL'
                    # Também garante que o Solid mostre cor de material
                    space.shading.color_type = 'MATERIAL'
                    _dbg(f"  Viewport configurado: shading=MATERIAL")
                    break


def load(operator, context, filepath, scale, merge_objects, flip_x, flip_y):
    try:
        with open(filepath, 'rb') as f:
            container = f.read()
    except Exception as e:
        operator.report({'ERROR'}, f"Nao foi possivel abrir o arquivo: {e}")
        return {'CANCELLED'}

    _dbg(f"Arquivo: {os.path.basename(filepath)}  ({len(container)} bytes)")

    # Limpa a cena antes de importar
    _clear_scene(context)

    raw_files = read_raw_files(container)
    if not raw_files:
        operator.report({'ERROR'}, "Nenhum sub-arquivo encontrado no container.")
        return {'CANCELLED'}

    mdl = RawMdl()
    mdl.parse(raw_files[0])

    textures = []
    if len(raw_files) > 1 and len(raw_files[1].data) > 0:
        _dbg(f"\n--- Lendo texturas do rawFile[1] ---")
        textures = parse_texture_array(raw_files[1].data)
        _dbg(f"  {len(textures)} textura(s) importada(s)")

    if not mdl.meshes:
        operator.report({'WARNING'}, "Nenhuma mesh encontrada.")
        return {'CANCELLED'}

    base_name = os.path.splitext(os.path.basename(filepath))[0]
    result = build_mesh(context, mdl, base_name=base_name, scale=scale,
                        flip_x=flip_x, flip_y=flip_y, merge_objects=merge_objects,
                        textures=textures)

    if result == {'FINISHED'}:
        # Configura viewport para Material Preview (mostra texturas)
        _setup_viewport(context)
        bone_info = f" com {len(mdl.bones)} bone(s)." if mdl.bones else "."
        tex_info  = f" {len(textures)} textura(s)." if textures else ""
        operator.report({'INFO'},
            f"Importadas {len(mdl.meshes)} mesh(es) de '{os.path.basename(filepath)}'"
            + bone_info + tex_info)
    return result