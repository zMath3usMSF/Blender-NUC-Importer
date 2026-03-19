bl_info = {
    "name": "SRAW Model Importer",
    "author": "NUCModelConverter Port",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "File > Import > SRAW Model (.sraw, .raw)",
    "description": "Imports .sraw and .raw model files as meshes in Blender",
    "category": "Import-Export",
}

import bpy
from bpy.types import Operator
from bpy.props import StringProperty, BoolProperty, CollectionProperty, FloatProperty

from bpy_extras.io_utils import ImportHelper
import os

from . importer import *


class SRAW_OT_import(Operator, ImportHelper):
    """Import a SRAW/RAW model file"""
    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    filepath: StringProperty(subtype='FILE_PATH')
    directory: StringProperty(subtype='DIR_PATH')

    bl_idname = "import_scene.sraw"
    bl_label = "Import SRAW Model"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".sraw"
    filter_glob: StringProperty(
        default="*.raw;*.sraw",
        options={'HIDDEN'},
    )

    scale: FloatProperty(
        name="Scale",
        description="Scale factor applied to the imported model",
        default=1.0,
        min=0.001,
        max=1000.0,
    )

    merge_objects: BoolProperty(
        name="Merge All Objects",
        description="Merge all sub-models into one single mesh object",
        default=False,
    )

    flip_x: BoolProperty(
        name="Flip X Axis",
        description="Negate X axis (matches the original converter behavior)",
        default=True,
    )

    flip_y: BoolProperty(
        name="Flip Y Axis",
        description="Negate Y axis (matches the original converter behavior)",
        default=True,
    )

    def execute(self, context):
        return importer.load(
            self,
            context,
        )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True

        col = layout.column()
        col.prop(self, "scale")
        col.prop(self, "merge_objects")

        col.separator()
        col.label(text="Axis Flipping:")
        col.prop(self, "flip_x")
        col.prop(self, "flip_y")


def menu_func_import(self, context):
    self.layout.operator(NUC_IMPORTER_OT_IMPORTER.bl_idname, text="SRAW Model (.sraw, .raw)")


def register():
    bpy.utils.register_class(NUC_IMPORTER_OT_IMPORTER)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.utils.unregister_class(NUC_IMPORTER_OT_IMPORTER)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)


if __name__ == "__main__":
    register()
