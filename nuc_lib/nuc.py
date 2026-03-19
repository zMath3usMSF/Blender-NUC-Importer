from .utils.PyBinaryReader.binary_reader import *

def readNUC(filePath):
    with open(filePath, "rb") as f:
        fileBytes = f.read()
        br = BinaryReader(fileBytes, encoding='cp932')
        value = br.read_int32()
        while value != -1:
            value = br.read_int32()