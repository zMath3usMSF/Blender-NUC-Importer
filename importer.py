import bpy
import bmesh
import os
import sys
import ctypes
from mathutils import Vector, Quaternion, Matrix
from bpy.props import StringProperty, BoolProperty, CollectionProperty, FloatProperty
from bpy_extras.io_utils import ImportHelper
from time import time

sys.path.insert(0, os.path.dirname(__file__))
from nuc_lib.nuc import readNUC, loadToBlender

DBG = True

class NUC_IMPORTER_OT_IMPORTER(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.sraw"
    bl_label = "Import NUC File"
    
    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    filepath: StringProperty(subtype='FILE_PATH')
    directory: StringProperty(subtype='DIR_PATH')
    scale: FloatProperty(name="Scale", default=0.128, min=0.001, max=1000.0)

    def execute(self, context):
        start_time = time()
        for file in self.files:
            self.filepath = os.path.join(self.directory, file.name)
            loadToBlender(self, context, self.filepath, self.scale)
        print(f"NUC files imported in {time() - start_time:.2f}s")
        return {'FINISHED'}
    
    def draw(self, context):
        self.layout.use_property_split = True