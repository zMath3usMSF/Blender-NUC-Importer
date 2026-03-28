from .utils.PyBinaryReader.binary_reader import *
from enum import Enum
from .nucArmature import nucArmature

class BlockType(Enum):
    ReadVertex = 0x0002
    ReadVertex2 = 0x0004
    ReadVertexUnk1A = 0x001A
    ReadVertexUnk1C = 0x001C
    SaveVertex = 0x0008
    SaveAndUpdateVertex = 0x000A

    ReadAndSaveUV = 0x000C

    ReadSystem = 0x0014

    ReadFace = 0x0018

    ReadAndSaveVertexNormal = 0x0020

    ReadAndSaveVertexColor = 0x0024

    ReadMeshColor = 0x002A

    MeshVertexInit = 0x002E

    ReadMeshColor2 = 0x0032

    MeshInit = 0x005E 

class Mesh(BrStruct):
    def __init__(self):
        self.meshFlags = []
        self.vertices = []
        self.normals = []
        self.color = []
        self.uvs = []
        self.faces = []
        self.meshData = []
        self.meshUnk1DataOffs = None
        self.meshUnk2DataOffs = None
        self.meshUnk1Flag = None
        self.meshUnk2Flag = None
        self.meshMaterialDataOffs = None
        self.meshUnkColor = None

    def __br_read__(self, br: BinaryReader):
        meshDataHeaderOffs = br.read_uint32()
        meshDataStartOffs = br.read_uint32()
        meshUnk1DataOffs = br.read_uint32()
        meshUnk2DataOffs = br.read_uint32()
        self.meshUnk1Flag = br.read_uint32()
        self.meshUnk2Flag = br.read_uint32()
        meshMaterialDataOffs = br.read_uint32()
        self.meshUnkColor = br.read_uint32()

        offset = meshDataStartOffs
        with br.seek_to(meshDataHeaderOffs):
            brChunk = BinaryReader(br.buffer())
            brChunk.seek(meshDataStartOffs)
            vertex_group = []
            normal_group = []
            color_group = []
            uvs_group = []
            chunkIndices = []
            faces_group = []    
            boneList = []    
            vertexCount = 0
            otherCount = 0
            while True:
                meshData = br.read_struct(MeshData)
                chunkData = []   
                chunkDataStart = brChunk.pos()
                if meshData.type == MeshDataType.Vertex:
                    boneList.append(meshData.boneIndex)
                while brChunk.pos() < chunkDataStart + meshData.length:
                    unk1 = brChunk.read_uint8()
                    unk2 = brChunk.read_uint8()
                    count = brChunk.read_uint8()
                    type = brChunk.read_uint8()

                    if type not in (0x0, 0x1, 0x14, 0x62):
                        chunkData = self.readChunk(brChunk, type, count)

                    if type == 0x62:
                        chunkIndices = self.readChunk(brChunk, type, count)

                    if type == 0x14:
                        try:
                            chunkType = BlockType(unk1)
                            if chunkType != BlockType.ReadFace:
                                    if len(faces_group) != 0:
                                        self.faces.append(faces_group)
                                        faces_group = []
                            if chunkType == BlockType.ReadVertex:
                                for i in range(len(chunkIndices)):
                                    if meshData.type == MeshDataType.Other:
                                        vertex = MeshVertex(chunkData[i], chunkIndices[i][0], boneList[otherCount - 1])
                                        vertex_group.append(vertex)
                                    else:
                                        vertex = MeshVertex(chunkData[i], chunkIndices[i][0], meshData.boneIndex)
                                        vertex_group.append(vertex)

                            elif chunkType == BlockType.ReadVertex2:
                                for i in range(len(chunkIndices)):
                                    if meshData.type == MeshDataType.Other:
                                        vertex = MeshVertex(chunkData[i], chunkIndices[i][0], boneList[otherCount - 1])
                                        vertex_group.append(vertex)
                                    else:
                                        vertex = MeshVertex(chunkData[i], chunkIndices[i][0], boneList[otherCount - 1])
                                        vertex_group.append(vertex)

                            elif chunkType in (BlockType.ReadVertexUnk1A, BlockType.ReadVertexUnk1C):
                                vertex_by_index = {v.index: v for v in vertex_group}
                                for i in range(len(chunkIndices)):
                                    idx = chunkIndices[i][0]
                                    if idx in vertex_by_index:
                                        br_v = BinaryReader(chunkData[i])
                                        x, y, z, w = br_v.read_float32(), br_v.read_float32(), br_v.read_float32(), br_v.read_float32()
                                        if meshData.type == MeshDataType.Other:
                                            vertex_by_index[idx].extraData.append((boneList[otherCount - 1], w, x, y, z))
                                        else:
                                            vertex_by_index[idx].extraData.append((meshData.boneIndex, w, x, y, z))
                                        vertex_by_index[idx].finalize_weights()

                            elif chunkType == BlockType.SaveVertex:
                                saved = []
                                for i in range(len(chunkIndices)):
                                    for j in range(len(vertex_group)):
                                        if chunkIndices[i][0] == vertex_group[j].index:
                                            saved.append(vertex_group[j])
                                            break
                                self.vertices.append(saved)
                                vertex_group = []
                                                        
                            elif chunkType == BlockType.SaveAndUpdateVertex:
                                if meshData.type == MeshDataType.Other:
                                    if len(faces_group) != 0:
                                        self.faces.append(faces_group)
                                        faces_group = []

                                vertex_by_index = {v.index: v for v in vertex_group}
                                last_vertex_group = [v.clone() for v in self.vertices[-1]]
                                
                                existing_indices = {v.index for v in last_vertex_group}
                                
                                # Substitui os que já existem
                                for i, v in enumerate(last_vertex_group):
                                    if v.index in vertex_by_index:
                                        last_vertex_group[i] = vertex_by_index[v.index]
                                
                                # Adiciona os que são novos (não estavam no grupo anterior)
                                for v in vertex_group:
                                    if v.index not in existing_indices:
                                        last_vertex_group.append(v)
                                
                                self.vertices.append(last_vertex_group)
                                vertex_group = []

                            elif chunkType == BlockType.ReadFace:
                                chunkData.pop(0)
                                for data in chunkData:
                                    faces_group.append(MeshFace(data, 0))

                            elif chunkType == BlockType.ReadAndSaveVertexNormal:
                                for data in chunkData:
                                    normal_group.append(MeshVertexNormal(data))
                                self.normals.append(normal_group)
                                normal_group = []

                            elif chunkType == BlockType.ReadAndSaveVertexColor:
                                for data in chunkData:
                                    color_group.append(MeshVertexColor(data))
                                self.color.append(color_group)
                                color_group = []

                            elif chunkType == BlockType.ReadAndSaveUV:
                                for data in chunkData:
                                    uvs_group.append(MeshUV(data))
                                self.uvs.append(uvs_group)
                                uvs_group = []
                        except:
                            print(f"Unk 0x{unk1:02X}")

                self.meshFlags.append(meshData)
                if meshData.type == MeshDataType.Vertex:
                    vertexCount += 1
                if meshData.type == MeshDataType.Other:
                    otherCount += 1
                    if len(faces_group) != 0:
                        self.faces.append(faces_group)
                        faces_group = []
                if meshData.type == MeshDataType.End:
                    break

            print(f"Position {brChunk.pos():02X}")

    def readChunk(self, br: BinaryReader, type, count):        
        chunk = []
        for i in range(count):
            if type == 0x62:
                chunk.append(br.read_bytes(0x1))
            elif type == 0x66:
                chunk.append(br.read_bytes(0x2))
            elif type == 0x68:
                chunk.append(br.read_bytes(0xC))
            elif type == 0x6A:
                chunk.append(br.read_bytes(0x3))
            elif type == 0x6C:
                chunk.append(br.read_bytes(0x10))
            elif type == 0x6E:
                chunk.append(br.read_bytes(0x4))
        
        while br.pos() % 4 != 0:
            br.seek(br.pos() + 1)        

        return chunk
    
class MeshDataType(Enum):
    End = 0x00
    InitSystem = 0x01
    Vertex = 0x02
    Other = 0x03
    InitModel = 0x04
    CelShade = 0x08

class MeshData(BrStruct):
    def __init__(self):
        self.type = None
        self.arg1 = None
        self.boneIndex = None
        self.length = None

    def __br_read__(self, br: BinaryReader):
        self.type = MeshDataType(br.read_uint8())
        self.arg1 = br.read_uint8()
        self.boneIndex = br.read_uint16()
        self.length = br.read_uint16() * 0x10

class MeshDataChunk:
    def __init__(self, blockType, data):
        self.blockType = blockType
        self.data = data

class MeshVertex:
    def __init__(self, chunkData, index, boneIndex):
        br = BinaryReader(chunkData)
        self.x = br.read_float32()
        self.y = br.read_float32()
        self.z = br.read_float32()
        self.weight = br.read_float32()
        self.index = index
        self.boneIndex = boneIndex
        self.extraData = []

    def clone(self):
        import copy
        c = copy.copy(self)
        c.extraData = list(self.extraData)  # copia a lista de extras
        return c

    def finalize_weights(self):
        """Chame depois de todos os extraData serem adicionados."""
        extra_sum = sum(w for _, w, *_ in self.extraData)
        self.weight = max(0.0, 1.0 - extra_sum)

class MeshVertexNormal:
    def __init__(self, chunkData):
        br = BinaryReader(chunkData)
        self.x = br.read_float32()
        self.y = br.read_float32()
        self.z = br.read_float32()
        self.index = br.read_uint32()

class MeshVertexColor:
    def __init__(self, chunkData):
        br = BinaryReader(chunkData)
        self.r = br.read_float32()
        self.g = br.read_float32()
        self.b = br.read_float32()
        self.a = br.read_float32()

class MeshUV:
    def __init__(self, chunkData):
        br = BinaryReader(chunkData)
        self.u = br.read_float32()
        self.v = br.read_float32()
        self.index = br.read_uint32()

class MeshFace:
    def __init__(self, chunkData, vertex_offset=0):
        br = BinaryReader(chunkData)
        self.vertex_index = br.read_uint8() + vertex_offset
        self.normal_index = br.read_uint8()
        self.uv_index = br.read_uint8()

class nucModel(BrStruct):
    def __init__(self):
        self.mesh = {}
        self.armature = None
        self.meshCount = 0
    
    def __br_read__(self, br: BinaryReader, Armature: nucArmature, meshCount):
        self.mesh = {i: br.read_struct(Mesh)
                for i in range(meshCount)}
            