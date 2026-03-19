from .utils.PyBinaryReader.binary_reader import *

class nucArmature(BrStruct):
    def __init__(self):
        self.unkFlag = None
        self.bones = {}

    def __br_read__(self, br: BinaryReader):
        self.boneCount = br.read_uint32()
        self.unkFlag = br.read_uint32()

        self.bones = {i: br.read_struct(Bone)
                      for i in range(self.boneCount)}
        
        self.boneParents = {i: br.read_uint16()
                            for i in range(self.boneCount)}
        
        self.finalize()
        
    def finalize(self):
        for i, bone in self.bones.items():
            parentIndex = self.boneParents[i]
            if parentIndex == i or parentIndex == 0xFFFF:
                bone.parent = None
            else:
                bone.parent = self.bones.get(parentIndex)


class Bone(BrStruct):
    def __init__(self):
        self.parent = None
        self.pos = (0,0,0,0)
        self.rot = (0,0,0,0)
        self.scale = (0,0,0,0)
        self.unkVector = (0,0,0,0)
    
    def __br_read__(self, br: BinaryReader):
        self.rot = br.read_float32(4)
        self.scale = br.read_float32(4)
        self.unkVector = br.read_float32(4)
        self.pos = br.read_float32(4)