"""Build a stylised 3D character in Blender from a validated spec (see character_builder.py).

Runs inside Blender only, started by AI Assist in the background:

    blender --background --factory-startup --python blender_character.py -- spec.json output_folder

Writes portrait.png (the avatar), character.blend and character.glb into the output
folder. Everything is made from Blender's own primitive shapes: no downloads, no models.

The character has a humanoid skeleton named after the VRM humanoid bones (hips, spine,
leftUpperArm, ...). Its rest pose is a T-pose, as Unreal Engine and VRM expect; the
portrait poses the arms down. character_builder.py turns the .glb into a .vrm.
"""
import json
import math
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

ARM_DROP_DEGREES = 72  # the portrait's relaxed arms, from the T-pose


def colour(hex_value):
    """sRGB '#rrggbb' to Blender's linear RGBA."""
    def linear(channel):
        channel /= 255
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
    value = hex_value.lstrip("#")
    return tuple(linear(int(value[index:index + 2], 16)) for index in (0, 2, 4)) + (1.0,)


_materials = {}


def material(name, hex_value, roughness=0.5, metallic=0.0):
    key = (name, hex_value)
    if key not in _materials:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = colour(hex_value)
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
        _materials[key] = mat
    return _materials[key]


def finish(obj, mat, parent, smooth=True):
    obj.data.materials.append(mat)
    if smooth:
        for polygon in obj.data.polygons:
            polygon.use_smooth = True
    obj.parent = parent
    return obj


def sphere(name, location, scale, mat, parent, segments=32):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=segments // 2, radius=1, location=location)
    obj = bpy.context.active_object
    obj.name, obj.scale = name, scale
    return finish(obj, mat, parent)


def cylinder(name, location, radius, depth, mat, parent, rotation=(0, 0, 0), vertices=32):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth, location=location, rotation=rotation)
    obj = bpy.context.active_object
    obj.name = name
    bevel = obj.modifiers.new("Soft edges", "BEVEL")
    bevel.width, bevel.segments = min(radius, depth) * 0.35, 3
    return finish(obj, mat, parent)


def torus(name, location, major, minor, mat, parent, rotation=(0, 0, 0)):
    bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=minor, location=location, rotation=rotation,
                                     major_segments=48, minor_segments=12)
    obj = bpy.context.active_object
    obj.name = name
    return finish(obj, mat, parent)


def hair_cap(head_center, radius, mat, parent, fringe):
    """A sphere around the head with the face cut away: the shape of most hairstyles."""
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=radius * 1.08, location=head_center)
    obj = bpy.context.active_object
    obj.name = "Hair"
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    cut = [vertex for vertex in mesh.verts
           if vertex.co.z < -radius * 0.15 or (vertex.co.y < -radius * 0.35 and vertex.co.z < radius * fringe)]
    bmesh.ops.delete(mesh, geom=cut, context="VERTS")
    mesh.to_mesh(obj.data)
    mesh.free()
    solid = obj.modifiers.new("Thickness", "SOLIDIFY")
    solid.thickness = radius * 0.08
    return finish(obj, mat, parent)


def _part(name):
    """Which body part a piece belongs to: everything above the neck moves with the head."""
    if name.startswith(("Leg", "Shoe")):
        return name.split(".")[0].split()[0].lower()
    if name.startswith(("Arm", "Hand")):
        return name.split(".")[0].split()[0].lower()
    if name.startswith("Body"):
        return "body"
    if name.startswith("Neck"):
        return "neck"
    return "head"


def _bone_for(part, point, size):
    side = "left" if point.x > 0 else "right"  # the character faces -Y, so its left is +X
    if part == "head" or part == "neck":
        return part
    if part == "body":
        if point.z < size["body_z"] - 0.05 * size["tall"]:
            return "hips"
        return "spine" if point.z < size["body_z"] + 0.07 * size["tall"] else "chest"
    if part == "hand":
        return side + "Hand"
    if part == "arm":
        return side + ("UpperArm" if abs(point.x) - size["shoulder_x"] < size["arm_length"] / 2 else "LowerArm")
    if part == "shoe":
        return side + "Foot"
    return side + ("UpperLeg" if point.z > size["knee_z"] else "LowerLeg")


def rig(root, size):
    """Join the body into one mesh and give it a humanoid skeleton (VRM bone names), rest pose = T-pose."""
    scene = bpy.context.scene
    pieces = [child for child in root.children if child.type == "MESH"]
    for piece in pieces:
        # Apply the soft-edge and thickness modifiers, so the joined mesh keeps the same shape.
        bpy.ops.object.select_all(action="DESELECT")
        piece.select_set(True)
        bpy.context.view_layer.objects.active = piece
        bpy.ops.object.convert(target="MESH")
        group = piece.vertex_groups.new(name="part:" + _part(piece.name))
        group.add(list(range(len(piece.data.vertices))), 1.0, "REPLACE")
    bpy.ops.object.select_all(action="DESELECT")
    for piece in pieces:
        piece.select_set(True)
    bpy.context.view_layer.objects.active = pieces[0]
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.ops.object.join()
    body = bpy.context.active_object
    body.name = "Avatar"

    hip_z, leg_x, knee_z = size["hip_z"], size["leg_x"], size["knee_z"]
    body_z, tall, neck_z, head_z, radius = size["body_z"], size["tall"], size["neck_z"], size["head_z"], size["radius"]
    sx, sz, arm = size["shoulder_x"], size["shoulder_z"], size["arm_length"]
    bones = [("hips", None, (0, 0, hip_z), (0, 0, body_z - 0.05 * tall)),
             ("spine", "hips", (0, 0, body_z - 0.05 * tall), (0, 0, body_z + 0.07 * tall)),
             ("chest", "spine", (0, 0, body_z + 0.07 * tall), (0, 0, neck_z)),
             ("neck", "chest", (0, 0, neck_z), (0, 0, head_z - radius * 0.9)),
             ("head", "neck", (0, 0, head_z - radius * 0.9), (0, 0, head_z + radius))]
    for side, sign in (("left", 1), ("right", -1)):
        bones += [(side + "UpperArm", "chest", (sign * sx, 0, sz), (sign * (sx + arm / 2), 0, sz)),
                  (side + "LowerArm", side + "UpperArm", (sign * (sx + arm / 2), 0, sz), (sign * (sx + arm), 0, sz)),
                  (side + "Hand", side + "LowerArm", (sign * (sx + arm), 0, sz), (sign * (sx + arm + 0.08), 0, sz)),
                  (side + "UpperLeg", "hips", (sign * leg_x, 0, hip_z), (sign * leg_x, 0, knee_z)),
                  (side + "LowerLeg", side + "UpperLeg", (sign * leg_x, 0, knee_z), (sign * leg_x, 0, 0.06)),
                  (side + "Foot", side + "LowerLeg", (sign * leg_x, 0, 0.06), (sign * leg_x, -0.12, 0.03))]

    armature = bpy.data.armatures.new("Skeleton")
    skeleton = bpy.data.objects.new(root.name, armature)
    scene.collection.objects.link(skeleton)
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = skeleton
    bpy.ops.object.mode_set(mode="EDIT")
    for name, parent, head, tail in bones:
        bone = armature.edit_bones.new(name)
        bone.head, bone.tail = head, tail
        if parent:
            bone.parent = armature.edit_bones[parent]
            bone.use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")

    # Each vertex follows one bone, chosen from the piece it came from and where it is.
    groups = {name: body.vertex_groups.new(name=name) for name, *_ in bones}
    parts = {group.index: group.name[5:] for group in body.vertex_groups if group.name.startswith("part:")}
    for vertex in body.data.vertices:
        part = next((parts[item.group] for item in vertex.groups if item.group in parts), "head")
        groups[_bone_for(part, vertex.co, size)].add([vertex.index], 1.0, "REPLACE")
    for group in [group for group in body.vertex_groups if group.name.startswith("part:")]:
        body.vertex_groups.remove(group)
    body.parent = skeleton
    body.modifiers.new("Skeleton", "ARMATURE").object = skeleton
    name = root.name
    bpy.data.objects.remove(root)
    skeleton.name = name  # the character's own name, now that the empty that held it is gone

    # The portrait shows relaxed arms; the saved rest pose stays a T-pose.
    for side, sign in (("left", 1), ("right", -1)):
        bone = skeleton.pose.bones[side + "UpperArm"]
        pivot = Vector((sign * sx, 0, sz))
        turn = Matrix.Translation(pivot) @ Matrix.Rotation(math.radians(sign * ARM_DROP_DEGREES), 4, "Y") @ Matrix.Translation(-pivot)
        bone.matrix = turn @ bone.bone.matrix_local
        bpy.context.view_layer.update()
    scene["ai_assist_skeleton"] = skeleton.name
    return skeleton


def build(spec):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    tall = {"short": 0.9, "medium": 1.0, "tall": 1.12}[spec["height"]]
    wide = {"slim": 0.85, "average": 1.0, "broad": 1.2}[spec["build"]]

    bpy.ops.object.empty_add(location=(0, 0, 0))
    root = bpy.context.active_object
    root.name = spec["name"] or "Character"

    skin = material("Skin", spec["skin"], roughness=0.55)
    top = material("Top", spec["top_color"], roughness=0.7)
    bottom = material("Bottom", spec["bottom_color"], roughness=0.75)
    shoes = material("Shoes", spec["shoe_color"], roughness=0.4)
    hair = material("Hair", spec["hair_color"], roughness=0.45)
    white = material("Eye white", "#f7f7f2", roughness=0.2)
    iris = material("Iris", spec["eye_color"], roughness=0.15)
    dark = material("Pupil", "#0b0b10", roughness=0.1)
    mouth = material("Mouth", "#7a2e35", roughness=0.4)
    cheek = material("Cheeks", "#f08a8a", roughness=0.6)

    # Legs, shoes, body, arms, hands (the camera looks along +Y, so the front is -Y).
    leg_height = 0.32 * tall
    for side in (-1, 1):
        cylinder("Leg", (side * 0.085 * wide, 0, leg_height / 2 + 0.04), 0.07 * wide, leg_height, bottom, root)
        sphere("Shoe", (side * 0.085 * wide, -0.025, 0.045), (0.075 * wide, 0.115, 0.05), shoes, root)
    body_z = leg_height + 0.17 * tall
    sphere("Body", (0, 0, body_z), (0.17 * wide, 0.13 * wide, 0.21 * tall), top, root)
    arm_length = 0.27 * tall
    shoulder_z = body_z + 0.13 * tall
    shoulder_x = 0.15 * wide
    for side in (-1, 1):
        # Built in a T-pose, the rest pose game engines expect; the skeleton lowers them for the portrait.
        shoulder = Vector((side * shoulder_x, 0, shoulder_z))
        hand = shoulder + Vector((side * arm_length, 0, 0))
        cylinder("Arm", (shoulder + hand) / 2, 0.048 * wide, arm_length, top, root, rotation=(0, math.radians(90), 0))
        sphere("Hand", hand + Vector((side * 0.02, 0, 0)), (0.055, 0.05, 0.055), skin, root)
    cylinder("Neck", (0, 0, body_z + 0.21 * tall), 0.05, 0.06, skin, root)

    # Head and face.
    radius = 0.23
    head = Vector((0, 0, body_z + 0.21 * tall + radius * 0.92))
    sphere("Head", head, (radius, radius * 0.95, radius * 0.98), skin, root, segments=48)
    for side in (-1, 1):
        eye = head + Vector((side * 0.08, -radius * 0.83, 0.015))
        sphere("Eye", eye, (0.048, 0.024, 0.055), white, root)
        sphere("Iris", eye + Vector((0, -0.016, -0.004)), (0.031, 0.012, 0.036), iris, root)
        sphere("Pupil", eye + Vector((0, -0.024, -0.004)), (0.014, 0.006, 0.017), dark, root)
        sphere("Sparkle", eye + Vector((side * -0.012, -0.029, 0.014)), (0.007, 0.003, 0.007), white, root)
        sphere("Cheek", head + Vector((side * 0.13, -radius * 0.78, -0.065)), (0.03, 0.01, 0.018), cheek, root)
        sphere("Ear", head + Vector((side * radius * 0.97, 0.01, 0)), (0.035, 0.045, 0.06), skin, root)
    torus("Smile", head + Vector((0, -radius * 0.9, -0.07)), 0.032, 0.0075, mouth, root,
          rotation=(math.radians(90), 0, 0)).scale = (1, 1, 0.55)

    style = spec["hair_style"]
    if style != "none":
        cap = hair_cap(head, radius, hair, root, fringe=0.42 if style != "spiky" else 0.5)
        if style == "long":
            sphere("Long hair", head + Vector((0, radius * 0.45, -radius * 0.75)),
                   (radius * 0.95, radius * 0.45, radius * 1.05), hair, root)
        elif style == "bun":
            sphere("Bun", head + Vector((0, radius * 0.45, radius * 0.95)), (0.09, 0.09, 0.09), hair, root)
        elif style == "curly":
            for index in range(26):
                angle = index * 2.399963  # golden angle: even spread over the cap
                height = 1 - (index + 0.5) / 26 * 1.1
                ring = math.sqrt(max(0.0, 1 - height * height))
                offset = Vector((math.cos(angle) * ring, abs(math.sin(angle) * ring) * 0.6 + 0.15, height))
                if offset.z > -0.1:
                    sphere("Curl", head + offset * radius * 1.07, (0.065, 0.065, 0.065), hair, root, segments=16)
        elif style == "spiky":
            for index, (x, z, tilt) in enumerate(((-0.12, 0.2, -30), (0, 0.24, 0), (0.12, 0.2, 30),
                                                  (-0.06, 0.16, -60), (0.06, 0.16, 60))):
                bpy.ops.mesh.primitive_cone_add(radius1=0.06, depth=0.16,
                                                location=head + Vector((x, 0.03, z)),
                                                rotation=(math.radians(-15), math.radians(tilt), 0))
                finish(bpy.context.active_object, hair, root).name = f"Spike {index + 1}"
        cap.name = "Hair"

    accessory = spec["accessory"]
    if accessory == "glasses":
        frame = material("Glasses", "#1d1f24", roughness=0.3, metallic=0.6)
        for side in (-1, 1):
            torus("Lens rim", head + Vector((side * 0.08, -radius * 0.98, 0.015)), 0.052, 0.008, frame, root,
                  rotation=(math.radians(90), 0, 0))
        cylinder("Bridge", head + Vector((0, -radius * 0.99, 0.02)), 0.006, 0.06, frame, root,
                 rotation=(0, math.radians(90), 0))
    elif accessory == "hat":
        hat = material("Hat", spec["top_color"], roughness=0.6)
        cylinder("Brim", head + Vector((0, 0, radius * 0.78)), radius * 1.35, 0.025, hat, root)
        cylinder("Crown", head + Vector((0, 0, radius * 1.05)), radius * 0.85, radius * 0.55, hat, root)
    elif accessory == "headphones":
        phones = material("Headphones", "#22252b", roughness=0.35, metallic=0.4)
        # The band runs from ear to ear over the top of the head.
        band = torus("Band", head + Vector((0, 0.01, 0.02)), radius * 1.12, 0.018, phones, root,
                     rotation=(math.radians(90), 0, 0))
        # Keep only the upper half of the ring (after the rotation, local Y points up).
        mesh = bmesh.new()
        mesh.from_mesh(band.data)
        bmesh.ops.delete(mesh, geom=[vertex for vertex in mesh.verts if vertex.co.y < -0.01], context="VERTS")
        mesh.to_mesh(band.data)
        mesh.free()
        for side in (-1, 1):
            cylinder("Ear cup", head + Vector((side * radius * 1.08, 0.01, 0)), 0.075, 0.05, phones, root,
                     rotation=(0, math.radians(90), 0))

    rig(root, {"hip_z": leg_height + 0.04, "leg_x": 0.085 * wide, "knee_z": leg_height / 2 + 0.06,
               "body_z": body_z, "tall": tall, "shoulder_x": shoulder_x, "shoulder_z": shoulder_z,
               "arm_length": arm_length, "neck_z": body_z + 0.18 * tall, "head_z": head.z, "radius": radius})

    # Light and camera for a friendly 3/4 portrait.
    total_height = head.z + radius
    focus = Vector((0, 0, total_height * 0.64))
    bpy.ops.object.empty_add(location=focus)
    target = bpy.context.active_object
    target.name = "Camera target"
    bpy.ops.object.camera_add(location=(1.1, -2.6, total_height * 0.78))
    camera = bpy.context.active_object
    camera.data.lens = 72
    look = camera.constraints.new("TRACK_TO")
    look.target, look.track_axis, look.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"
    scene.camera = camera
    for name, location, energy, size in (("Key", (2.2, -2.4, 2.6), 520, 2.5), ("Fill", (-2.5, -1.6, 1.4), 160, 3.0),
                                         ("Rim", (-0.6, 2.4, 2.2), 380, 1.5)):
        bpy.ops.object.light_add(type="AREA", location=location)
        light = bpy.context.active_object
        light.name, light.data.energy, light.data.size = name, energy, size
        aim = light.constraints.new("TRACK_TO")
        aim.target, aim.track_axis, aim.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"
    world = bpy.data.worlds.new("Backdrop")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = colour(spec["backdrop"])
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.9
    scene.world = world
    bpy.ops.mesh.primitive_circle_add(vertices=64, radius=0.6, fill_type="NGON", location=(0, 0, 0.001))
    finish(bpy.context.active_object, material("Floor", spec["backdrop"], roughness=0.9), None, smooth=False).name = "Floor"
    return scene


def render_and_save(scene, output):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    denoise = bool(getattr(bpy.app.build_options, "openimagedenoise", False))
    scene.cycles.use_denoising = denoise
    scene.cycles.samples = 64 if denoise else 192  # without the denoiser, more samples keep it smooth
    scene.render.resolution_x = scene.render.resolution_y = 640
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = os.path.join(output, "portrait.png")
    bpy.ops.render.render(write_still=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(output, "character.blend"))
    # Export only the character (skeleton and body) in its T-pose rest position, for game engines.
    skeleton = bpy.data.objects[scene["ai_assist_skeleton"]]
    bpy.ops.object.select_all(action="DESELECT")
    skeleton.select_set(True)
    for child in skeleton.children:
        child.select_set(True)
    bpy.context.view_layer.objects.active = skeleton
    options = dict(filepath=os.path.join(output, "character.glb"), export_format="GLB", use_selection=True,
                   export_skins=True, export_animations=False)
    try:
        try:
            bpy.ops.export_scene.gltf(**options, export_rest_position_armature=True)
        except TypeError:  # Blender before 3.6 has no rest-position option; it exports the rest pose anyway
            bpy.ops.export_scene.gltf(**options)
    except Exception as error:  # Some Linux builds of Blender lack the exporter's numpy; the rest still counts.
        print("AI_ASSIST_CHARACTER_NO_GLB", error)


def main():
    arguments = sys.argv[sys.argv.index("--") + 1:]
    spec_path, output = arguments[0], arguments[1]
    with open(spec_path, encoding="utf-8") as source:
        spec = json.load(source)
    render_and_save(build(spec), output)
    print("AI_ASSIST_CHARACTER_DONE")


if __name__ == "__main__":
    main()
