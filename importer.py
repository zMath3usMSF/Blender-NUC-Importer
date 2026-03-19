import bpy
import bmesh
import struct
import os
import ctypes
from mathutils import Vector, Quaternion, Matrix
from bpy.props import StringProperty, BoolProperty, CollectionProperty, FloatProperty
from bpy_extras.io_utils import ImportHelper
from time import perf_counter, time
from .nuc_lib.nuc import *

DBG = True

_ea_swizzle_dll = None

class NUC_IMPORTER_OT_IMPORTER(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.sraw"
    bl_label = "Import NUC File"
    
    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    filepath: StringProperty(subtype='FILE_PATH')
    directory: StringProperty(subtype='DIR_PATH')

    def execute(self, context):
        start_time = time()
        nucFiles = []

        for file in self.files:

            self.filepath = os.path.join(self.directory, file.name)
            
            nucFile = readNUC(self.filepath)

            nucFiles.append(nucFile)

        elapsed_s = "{:.2f}s".format(time() - start_time)
        print(f"NUC files imported in " + elapsed_s)
        
        return {'FINISHED'}
    
    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True