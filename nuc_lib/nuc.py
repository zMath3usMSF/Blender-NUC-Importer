from .utils.PyBinaryReader.binary_reader import *
from .nucArmature import nucArmature
from .nucModel import nucModel

class nucFile(BrStruct):
    def __init__(self):
        self.type = ""
        self.chunks = {}
        self.armature = None
        self.model = None
    
    def __br_read__(self, br: BinaryReader):
        self.type = "ModelFile"
        if self.type == "ModelFile":
            meshHeaderOffset = br.read_uint32()
            meshCount = br.read_uint32()
            self.armature = br.read_struct(nucArmature)
            self.model = nucModel.__new__(nucModel)
            self.model.__init__()
            self.model.__br_read__(br, self.armature, meshCount)
            print("!")

def readNUC(filePath):
    with open(filePath, "rb") as f:
        fileBytes = f.read()
    br = BinaryReader(fileBytes, encoding='cp932')
    nuc = br.read_struct(nucFile)
    return nuc

def loadToBlender(operator, context, filepath, scale=1.0):
    import sys, os
    # sobe um nível para achar o nucBlender na raiz do addon
    addon_dir = os.path.dirname(os.path.dirname(__file__))
    if addon_dir not in sys.path:
        sys.path.insert(0, addon_dir)
    from nucBlender import load
    return load(operator, context, filepath, scale)