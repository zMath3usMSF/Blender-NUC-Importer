from .utils.PyBinaryReader.binary_reader import *
from .nucArmature import nucArmature

class nucFile(BrStruct):
    def __init__(self):
        self.type = ""
        self.chunks = {}
    
    def __br_read__(self, br: BinaryReader):
        self.type = "ModelFile"

        if self.type == "ModelFile":
            meshHeaderOffset = br.read_uint32()
            meshCount = br.read_uint32()
            armature = br.read_struct(nucArmature)
            print("!")

def readNUC(filePath):
    with open(filePath, "rb") as f:
        fileBytes = f.read()
    br = BinaryReader(fileBytes, encoding='cp932')
    nuc = br.read_struct(nucFile)
    return nuc