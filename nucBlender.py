from collections import defaultdict
import bpy
import bmesh
from mathutils import Vector, Matrix, Quaternion
import math

from nuc_lib.nuc import readNUC
from nuc_lib.nucModel import MeshVertex, MeshVertexNormal, MeshUV, MeshFace


# ── Armadura ──────────────────────────────────────────────────────────────────

AXIS_CONV = Matrix.Rotation(math.radians(-90), 4, 'X')


def _bone_world_matrix(bones: dict, bone_idx: int, cache: dict) -> Matrix:
    if bone_idx in cache:
        return cache[bone_idx]

    bone = bones.get(bone_idx)
    if bone is None:
        cache[bone_idx] = Matrix.Identity(4)
        return cache[bone_idx]

    x, y, z, w = bone.rot
    sx, sy, sz = bone.scale[:3]

    rot_mat   = Quaternion((w, x, y, -z)).to_matrix().to_4x4()
    scale_mat = Matrix.Diagonal((sx, sy, sz, 1.0))
    loc_mat   = Matrix.Translation(bone.pos[:3]) @ rot_mat @ scale_mat

    parent_idx = None
    if bone.parent:
        parent_idx = next((i for i, b in bones.items() if b is bone.parent), None)

    if parent_idx is not None:
        world_mat = _bone_world_matrix(bones, parent_idx, cache) @ loc_mat
    else:
        world_mat = loc_mat

    cache[bone_idx] = world_mat
    return world_mat


def build_armature(context, armature, base_name: str, scale: float = 1.0):
    if not armature or not armature.bones:
        return None

    arm_data               = bpy.data.armatures.new(f"{base_name}_armature")
    arm_data.display_type  = 'STICK'
    arm_data.show_axes     = False

    arm_obj               = bpy.data.objects.new(f"{base_name}_armature", arm_data)
    arm_obj.show_in_front = True
    context.collection.objects.link(arm_obj)

    context.view_layer.objects.active = arm_obj
    bpy.ops.object.select_all(action='DESELECT')
    arm_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')

    cache, edit_bones = {}, {}

    for idx, bone in armature.bones.items():
        eb             = arm_data.edit_bones.new(f"bone_{idx:02d}")
        eb.use_connect = False

        converted     = AXIS_CONV @ _bone_world_matrix(armature.bones, idx, cache)
        head          = converted.translation * scale
        m             = converted.to_3x3().to_4x4()
        m.translation = head
        eb.matrix     = m
        eb.tail       = (converted @ Vector((0, 0.05, 0))) * scale

        edit_bones[idx] = eb

    for idx, bone in armature.bones.items():
        parent_idx = None
        if bone.parent:
            parent_idx = next((i for i, b in armature.bones.items() if b is bone.parent), None)
        if parent_idx in edit_bones:
            edit_bones[idx].parent = edit_bones[parent_idx]

    bpy.ops.object.mode_set(mode='OBJECT')
    return arm_obj


# ── Posição do vértice ────────────────────────────────────────────────────────

def _vertex_position(v: MeshVertex, armature, cache: dict, scale: float) -> Vector:
    local = Vector((v.x, v.y, v.z))
    bone_mat = _bone_world_matrix(armature.bones, v.boneIndex, cache) if armature and v.boneIndex in armature.bones else Matrix.Identity(4)
    pos_base = (AXIS_CONV @ bone_mat) @ local
    pos = pos_base * v.weight

    if v.extraData:
        print(f"[VTX idx={v.index} bone={v.boneIndex}]")
        print(f"  base xyz=({v.x:.3f}, {v.y:.3f}, {v.z:.3f}) w={v.weight:.3f} → {pos_base}")
        for bone_idx, weight, x, y, z in v.extraData:
            extra_mat = _bone_world_matrix(armature.bones, bone_idx, cache) if armature and bone_idx in armature.bones else Matrix.Identity(4)
            contrib = (AXIS_CONV @ extra_mat) @ Vector((x, y, z))
            print(f"  extra bone={bone_idx} xyz=({x:.3f},{y:.3f},{z:.3f}) w={weight:.3f} → {contrib}")
            pos += contrib * weight

        print(f"  final pos = {pos * scale}")
    else:
        pos = pos_base * v.weight

    return pos * scale


# ── Sub-mesh ──────────────────────────────────────────────────────────────────

def build_submesh(context, vertices, normals, uvs, faces,
                  submesh_idx, armature, arm_obj,
                  base_name: str, scale: float = 1.0, vertex_offset=0):
    if not vertices or not faces:
        return None

    cache = {}

    verts_co = [_vertex_position(v, armature, cache, scale) for v in vertices]

    bm       = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")
    bm_verts = [bm.verts.new(co) for co in verts_co]

    index_to_pos = {v.index: i for i, v in enumerate(vertices)}
    uv_to_pos    = {uv.index: i for i, uv in enumerate(uvs)}

    # DEBUG — posição correta, antes do loop
    face_indices = set(f.vertex_index for f in faces)
    vert_indices = set(v.index for v in vertices)
    missing = face_indices - vert_indices
    if missing:
        print(f"  [DEBUG {submesh_idx}] v.index range: {min(vert_indices)}-{max(vert_indices)}")
        print(f"  [DEBUG {submesh_idx}] face refs faltando: {sorted(missing)}")

    bm.verts.ensure_lookup_table()
    bm.verts.index_update()

    max_vi = max((f.vertex_index for f in faces), default=-1)
    max_ui = max((f.uv_index for f in faces), default=-1)
    print(
        f"[submesh {submesh_idx}] "
        f"verts={len(vertices)} faces={len(faces)} uvs={len(uvs)} "
        f"max_vertex_index={max_vi} max_uv_index={max_ui}"
    )

    skipped_range = 0
    skipped_degenerate = 0
    skipped_duplicate = 0

    i = 0
    while i + 2 < len(faces):
        f0, f1, f2 = faces[i], faces[i + 1], faces[i + 2]

        va = index_to_pos.get(f0.vertex_index, -1)
        vb = index_to_pos.get(f1.vertex_index, -1)
        vc = index_to_pos.get(f2.vertex_index, -1)
        ua = uv_to_pos.get(f0.uv_index, -1)
        ub = uv_to_pos.get(f1.uv_index, -1)
        uc = uv_to_pos.get(f2.uv_index, -1)

        if va == -1 or vb == -1 or vc == -1:
            skipped_range += 1
            i += 3
            continue

        if va == vb or vb == vc or va == vc:
            skipped_degenerate += 1
            i += 3
            continue

        try:
            face = bm.faces.new((bm_verts[va], bm_verts[vb], bm_verts[vc]))
            face.smooth = True
            for loop, ui in zip(face.loops, (ua, ub, uc)):
                if ui != -1:
                    loop[uv_layer].uv = (uvs[ui].u, 1.0 - uvs[ui].v)
        except ValueError:
            skipped_duplicate += 1

        i += 3

    total_skipped = skipped_range + skipped_degenerate + skipped_duplicate
    if total_skipped:
        print(
            f"[submesh {submesh_idx}] {total_skipped} face(s) ignorada(s): "
            f"{skipped_range} fora do range, "
            f"{skipped_degenerate} degeneradas (índices iguais), "
            f"{skipped_duplicate} duplicadas (ValueError)"
        )

    obj_name  = f"{base_name}_{submesh_idx}"
    mesh_data = bpy.data.meshes.new(obj_name)
    obj       = bpy.data.objects.new(obj_name, mesh_data)
    context.collection.objects.link(obj)
    bm.to_mesh(mesh_data)
    bm.free()

    if normals and len(normals) == len(mesh_data.vertices):
        cache_normals  = {}
        custom_normals = []
        for n in normals:
            if armature and hasattr(n, 'index') and n.index in armature.bones:
                bone_mat = _bone_world_matrix(armature.bones, n.index, cache_normals)
                nv = bone_mat.to_3x3() @ Vector((n.x, n.y, n.z))
            else:
                nv = Vector((n.x, n.y, n.z))
            custom_normals.append(nv.normalized())
        mesh_data.normals_split_custom_set_from_vertices(custom_normals)

    vg_map = {}
    for vi, v in enumerate(vertices):
        vg_name = f"bone_{v.boneIndex:02d}"
        if vg_name not in vg_map:
            vg_map[vg_name] = obj.vertex_groups.new(name=vg_name)
        vg_map[vg_name].add([vi], 1.0, 'REPLACE')

    if arm_obj:
        obj.parent = arm_obj
        obj.matrix_parent_inverse = arm_obj.matrix_world.inverted()
        mod = obj.modifiers.new(name="Armature", type='ARMATURE')
        mod.object = arm_obj
        mod.use_vertex_groups = True

    return obj

# ── Mesh (itera sub-meshes) ───────────────────────────────────────────────────

def build_mesh(context, nuc_mesh, mesh_idx, armature, arm_obj, base_name, scale=1.0):
    num_submeshes = max(len(nuc_mesh.vertices), len(nuc_mesh.faces), 1)

    # Offset acumulado por grupo
    offsets = [0]
    for si in range(len(nuc_mesh.vertices) - 1):
        offsets.append(offsets[-1] + len(nuc_mesh.vertices[si]))

    created = []
    for si in range(num_submeshes):
        vertices = nuc_mesh.vertices[si] if si < len(nuc_mesh.vertices) else []
        faces    = nuc_mesh.faces[si]    if si < len(nuc_mesh.faces)    else []
        uvs      = nuc_mesh.uvs[si]      if si < len(nuc_mesh.uvs)      else []
        normals  = nuc_mesh.normals[si]  if si < len(nuc_mesh.normals)  else []

        offset = offsets[si] if si < len(offsets) else 0

        obj = build_submesh(
            context, vertices, normals, uvs, faces,
            submesh_idx=f"{mesh_idx}_{si}",
            armature=armature,
            arm_obj=arm_obj,
            base_name=base_name,
            scale=scale,
            vertex_offset=offset,   # ← passa o offset separado
        )
        if obj:
            created.append(obj)

    return created

# ── Entry point ───────────────────────────────────────────────────────────────

def load(operator, context, filepath, scale=1.0):
    nuc = readNUC(filepath)

    if nuc.model is None:
        operator.report({'ERROR'}, "Nenhum modelo encontrado no arquivo.")
        return {'CANCELLED'}

    import os
    base_name = os.path.splitext(os.path.basename(filepath))[0]
    armature  = nuc.armature

    arm_obj = build_armature(context, armature, base_name, scale)

    created = []
    for mesh_idx, mesh in nuc.model.mesh.items():
        objs = build_mesh(context, mesh, mesh_idx, armature,
                          arm_obj, base_name, scale)
        created.extend(objs)

    if not created:
        operator.report({'WARNING'}, "Nenhuma mesh importada.")
        return {'CANCELLED'}

    for o in context.selected_objects:
        o.select_set(False)
    for o in created + ([arm_obj] if arm_obj else []):
        if o:
            o.select_set(True)
    if arm_obj:
        context.view_layer.objects.active = arm_obj

    operator.report({'INFO'},
        f"Importado: {len(created)} sub-mesh(es), "
        f"{len(armature.bones) if armature else 0} bone(s).")
    return {'FINISHED'}