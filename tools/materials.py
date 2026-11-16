"""Build the Skyfall materials in Unreal Editor, with all distances in cm.

Call ``build_materials()`` from the scene builder. Re-running it updates the
same assets and checks every material connection and shader compilation.
The photographs are the bundled CC0 Poly Haven files in SourceAssets/Textures.
Normal maps were prepared for Blender, so their green channel is flipped on
import for Unreal's DirectX tangent-space convention.
"""

from pathlib import Path
import json

import unreal


ASSET_DIRECTORY = Path(__file__).resolve().parents[1] / "SourceAssets" / "Textures"
TEXTURE_DIRECTORY = "/Game/Skyfall/Textures"
MATERIAL_DIRECTORY = "/Game/Skyfall/Materials"
FUNCTION_DIRECTORY = "/Engine/Functions/Engine_MaterialFunctions01/Texturing"
TEXTURE_SOURCES = {
    "concrete_surface.jpg": {
        "asset": "T_Concrete_Surface",
        "source": "https://polyhaven.com/a/concrete",
        "license": "CC0",
        "license_url": "https://polyhaven.com/license",
    },
    "concrete_surface_normal.png": {
        "asset": "T_Concrete_Surface_Normal",
        "source": "https://polyhaven.com/a/concrete",
        "license": "CC0",
        "license_url": "https://polyhaven.com/license",
    },
    "asphalt.jpg": {
        "asset": "T_Asphalt",
        "source": "https://polyhaven.com/a/asphalt_02",
        "license": "CC0",
        "license_url": "https://polyhaven.com/license",
    },
}

MATERIAL_NAMES = {
    "concrete": "M_Concrete",
    "fracture": "M_Fracture",
    "glass": "M_Glass",
    "metal": "M_Rebar_Rust",
    "asphalt": "M_Asphalt",
    "paint": "M_Safety_Paint",
    "soil": "M_Soil",
    "brick": "M_Brick",
    "dust": "M_Dust",
    "aluminum": "M_Anodized_Aluminum",
    "plaster": "M_Warm_Plaster",
    "wood": "M_Smoked_Oak",
    "fabric": "M_Seat_Fabric",
    "bronze": "M_Warm_Bronze",
    "foliage": "M_Foliage",
    "stainless": "M_Brushed_Stainless",
    "rubber": "M_EPDM_Seal",
    "linen": "M_Unbleached_Linen",
    "luminaire": "M_Warm_Luminaire",
    "glasstint": "M_Glass_Warm_Tint",
}

EDIT = unreal.MaterialEditingLibrary
PROPERTY = unreal.MaterialProperty


def _import_textures():
    """Import the supplied sources once, then enforce color/normal settings."""
    textures = {}
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    for filename, provenance in TEXTURE_SOURCES.items():
        source = ASSET_DIRECTORY / filename
        if not source.is_file():
            raise FileNotFoundError(f"Missing material source {source}")
        asset_path = f"{TEXTURE_DIRECTORY}/{provenance['asset']}"
        texture = unreal.load_asset(asset_path)
        if texture is None:
            task = unreal.AssetImportTask()
            task.set_editor_property("filename", str(source))
            task.set_editor_property("destination_path", TEXTURE_DIRECTORY)
            task.set_editor_property("destination_name", provenance["asset"])
            task.set_editor_property("automated", True)
            task.set_editor_property("replace_existing", False)
            task.set_editor_property("save", False)
            task.set_editor_property("factory", unreal.TextureFactory())
            asset_tools.import_asset_tasks([task])
            texture = unreal.load_asset(asset_path)
        if not isinstance(texture, unreal.Texture2D):
            raise RuntimeError(f"Texture import did not produce Texture2D {asset_path}")
        is_normal = filename.endswith("_normal.png")
        texture.set_editor_property("srgb", not is_normal)
        texture.set_editor_property(
            "compression_settings",
            unreal.TextureCompressionSettings.TC_NORMALMAP
            if is_normal else unreal.TextureCompressionSettings.TC_DEFAULT,
        )
        texture.set_editor_property("flip_green_channel", is_normal)
        texture.set_editor_property("virtual_texture_streaming", False)
        unreal.EditorAssetLibrary.set_metadata_tag(texture, "Source", provenance["source"])
        unreal.EditorAssetLibrary.set_metadata_tag(texture, "License", "CC0")
        if not unreal.EditorAssetLibrary.save_loaded_asset(texture, only_if_is_dirty=False):
            raise RuntimeError(f"Could not save texture {asset_path}")
        textures[filename] = texture
    return textures


class _Graph:
    """Small readable helpers around MaterialEditingLibrary's graph operations."""

    def __init__(self, key):
        self.key = key
        self.asset_path = f"{MATERIAL_DIRECTORY}/{MATERIAL_NAMES[key]}"
        self.material = unreal.load_asset(self.asset_path)
        if self.material is None:
            self.material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
                MATERIAL_NAMES[key], MATERIAL_DIRECTORY,
                unreal.Material, unreal.MaterialFactoryNew(),
            )
        if not isinstance(self.material, unreal.Material):
            raise RuntimeError(f"Material asset could not be created {self.asset_path}")
        # Delete from a Python snapshot because removal mutates the native array.
        for expression in list(EDIT.get_material_expressions(self.material)):
            EDIT.delete_material_expression(self.material, expression)
        self.material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_OPAQUE)
        self.material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
        self.material.set_editor_property("two_sided", False)
        # WorldAlignedNormal returns a world-space vector.
        self.material.set_editor_property("tangent_space_normal", False)
        self.material.set_editor_property("used_with_instanced_static_meshes", True)
        self.material.set_editor_property(
            "used_with_geometry_collections", key in {"concrete", "fracture", "metal", "brick"},
        )

    def node(self, expression_class, caption, **properties):
        expression = EDIT.create_material_expression(self.material, expression_class)
        if expression is None:
            raise RuntimeError(f"Could not create {expression_class} in {self.asset_path}")
        expression.set_editor_property("desc", caption)
        for name, value in properties.items():
            expression.set_editor_property(name, value)
        return expression

    def link(self, source, destination, input_name="", output_name=""):
        if not EDIT.connect_material_expressions(source, output_name, destination, input_name):
            inputs = list(EDIT.get_material_expression_input_names(destination))
            outputs = list(EDIT.get_material_expression_output_names(source))
            raise RuntimeError(
                f"{self.asset_path} failed connection {output_name!r} -> {input_name!r}. "
                f"Available outputs {outputs}, inputs {inputs}"
            )

    def output(self, source, material_property, output_name=""):
        if not EDIT.connect_material_property(source, output_name, material_property):
            raise RuntimeError(f"Failed material output {self.asset_path} {material_property}")

    def scalar(self, value, description):
        return self.node(unreal.MaterialExpressionConstant, description, r=float(value))

    def color(self, value, description):
        return self.node(
            unreal.MaterialExpressionConstant3Vector, description,
            constant=unreal.LinearColor(*value, 1.0),
        )

    def parameter(self, name, value, description):
        return self.node(
            unreal.MaterialExpressionScalarParameter, description,
            parameter_name=name, default_value=float(value), group="Dust Lifetime",
        )

    def operation(self, expression_class, left, right, description):
        result = self.node(expression_class, description)
        self.link(left, result, "A")
        self.link(right, result, "B")
        return result

    def multiply(self, left, right, description):
        return self.operation(unreal.MaterialExpressionMultiply, left, right, description)

    def lerp(self, low, high, alpha, description):
        result = self.node(unreal.MaterialExpressionLinearInterpolate, description)
        self.link(low, result, "A")
        self.link(high, result, "B")
        self.link(alpha, result, "Alpha")
        return result

    def noise(self, scale_cm, description, levels=2):
        position = self.node(unreal.MaterialExpressionWorldPosition, "Absolute world position in cm")
        noise = self.node(
            unreal.MaterialExpressionNoise, description,
            scale=1.0 / scale_cm, levels=levels, quality=1,
            noise_function=unreal.NoiseFunction.NOISEFUNCTION_GRADIENT_TEX3D,
            output_min=0.0, output_max=1.0, turbulence=False,
        )
        self.link(position, noise, "World Position")
        return noise

    def world_texture(self, texture, tile_cm, description, normal=False):
        """Triplanar mapping keeps density consistent on scaled geometry."""
        texture_object = self.node(
            unreal.MaterialExpressionTextureObject, description + " source",
            texture=texture,
            sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL
            if normal else unreal.MaterialSamplerType.SAMPLERTYPE_COLOR,
        )
        function_name = "WorldAlignedNormal" if normal else "WorldAlignedTexture"
        function = unreal.load_asset(f"{FUNCTION_DIRECTORY}/{function_name}")
        if function is None:
            raise RuntimeError(f"Missing engine function {function_name}")
        call = self.node(unreal.MaterialExpressionMaterialFunctionCall, description)
        if not call.set_material_function(function):
            raise RuntimeError(f"Could not assign engine function {function_name}")
        self.link(texture_object, call, "TextureObject")
        self.link(self.color((tile_cm, tile_cm, tile_cm), "Texture repeat distance in cm"), call, "TextureSize")
        # Both engine functions expose this three-axis projection output.
        result = self.node(unreal.MaterialExpressionReroute, description + " XYZ projection")
        self.link(call, result, output_name="XYZ Texture")
        return result

    def finish(self):
        EDIT.layout_material_expressions(self.material)
        errors = list(EDIT.recompile_material(self.material))
        # Resolve shader workers before accepting or saving a rendered material.
        unreal.SystemLibrary.execute_console_command(None, "Editor.AsyncAssetCompilationFinishAll")
        if errors:
            raise RuntimeError(f"Shader compile failed {self.asset_path}\n" + "\n".join(errors))
        if not unreal.EditorAssetLibrary.save_loaded_asset(self.material, only_if_is_dirty=False):
            raise RuntimeError(f"Could not save material {self.asset_path}")
        unreal.log(f"SKYFALL_MATERIAL_OK {self.key} {self.asset_path}")
        return self.material


def _textured_solid(key, textures, tint, tile_cm, roughness, normal_tile_cm=None):
    graph = _Graph(key)
    graph.material.set_editor_property("tangent_space_normal", False)
    source = textures["asphalt.jpg"] if key == "asphalt" else textures["concrete_surface.jpg"]
    photograph = graph.world_texture(source, tile_cm, "Photographic surface at physical scale")
    tinted = graph.multiply(photograph, graph.color(tint, "Surface albedo tint"), "Photographic albedo")
    if key == "concrete":
        tinted = graph.lerp(graph.color((0.30, 0.32, 0.335), "Neutral cast cement"),
                            tinted, graph.scalar(0.24, "Subtle formwork variation"),
                            "Restrained concrete surface pattern")
    stains = graph.noise(430.0, "Broad world-aligned weathering breaks texture repetition")
    weathering = graph.lerp(
        graph.scalar(0.78, "Damp and weathered patches"),
        graph.scalar(1.04, "Dry lighter patches"), stains, "Subtle irregular surface value",
    )
    color = graph.multiply(tinted, weathering, "Weathered albedo")
    grain = graph.noise(2.5, "Fine aggregate roughness in cm")
    rough = graph.lerp(
        graph.scalar(roughness[0], "Smoother aggregate roughness"),
        graph.scalar(roughness[1], "Porous aggregate roughness"), grain, "Roughness variation",
    )
    normal = graph.world_texture(
        textures["concrete_surface_normal.png"], normal_tile_cm or tile_cm,
        "Photographic aggregate normal", normal=True,
    )
    graph.output(color, PROPERTY.MP_BASE_COLOR)
    graph.output(rough, PROPERTY.MP_ROUGHNESS)
    surface_normal = graph.node(unreal.MaterialExpressionVertexNormalWS, "Geometric world-space normal")
    strength = 0.0 if key == "asphalt" else (0.10 if key == "concrete" else 0.22)
    restrained_normal = graph.lerp(surface_normal, normal,
                                   graph.scalar(strength, "Restrained photographic relief"),
                                   "Blend world-space surface normals")
    normalized = graph.node(unreal.MaterialExpressionNormalize, "Unit length world normal")
    graph.link(restrained_normal, normalized, "VectorInput")
    graph.output(normalized, PROPERTY.MP_NORMAL)
    graph.output(graph.scalar(0.0, "Dielectric surface"), PROPERTY.MP_METALLIC)
    graph.output(graph.scalar(0.28, "Restrained dielectric reflection"), PROPERTY.MP_SPECULAR)
    return graph, color


def _build_rust(textures):
    graph = _Graph("metal")
    rust = graph.noise(4.0, "Uneven corrosion across exposed reinforcing steel", levels=3)
    graph.output(graph.lerp(
        graph.color((0.065, 0.052, 0.041), "Old dark steel"),
        graph.color((0.29, 0.085, 0.023), "Iron oxide orange brown"), rust, "Corroded steel color",
    ), PROPERTY.MP_BASE_COLOR)
    graph.output(graph.lerp(
        graph.scalar(0.64, "Exposed steel reflectivity"),
        graph.scalar(0.04, "Non-metallic corrosion"), rust, "Rust reduces metallic response",
    ), PROPERTY.MP_METALLIC)
    graph.output(graph.lerp(
        graph.scalar(0.57, "Worn steel roughness"),
        graph.scalar(0.94, "Porous rust roughness"), rust, "Pitted surface roughness",
    ), PROPERTY.MP_ROUGHNESS)
    normal = graph.world_texture(textures["concrete_surface_normal.png"], 16.0, "Small corrosion pits", normal=True)
    graph.output(normal, PROPERTY.MP_NORMAL)
    return graph.finish()


def _build_brick(textures):
    graph, brick_color = _textured_solid(
        "brick", textures, (0.64, 0.21, 0.105), 70.0, (0.78, 0.97), 48.0,
    )
    position_input = unreal.CustomInput()
    position_input.set_editor_property("input_name", "WorldPosition")
    normal_input = unreal.CustomInput()
    normal_input.set_editor_property("input_name", "WorldNormal")
    # A 24 x 7.5 cm running bond, with a 0.8 cm mortar joint and soft AA edges.
    mortar = graph.node(
        unreal.MaterialExpressionCustom, "24 x 7.5 cm running-bond brick joints",
        description="World-space brick coursing",
        output_type=unreal.CustomMaterialOutputType.CMOT_FLOAT1,
        code="""
float3 faceNormal = abs(WorldNormal);
float horizontal = faceNormal.x > faceNormal.y ? WorldPosition.y : WorldPosition.x;
float vertical = WorldPosition.z;
if (faceNormal.z > 0.7)
{
    horizontal = WorldPosition.x;
    vertical = WorldPosition.y;
}
float row = floor(vertical / 7.5);
float stagger = frac(row * 0.5) * 24.0;
float2 withinBrick = frac(float2(horizontal + stagger, vertical) / float2(24.0, 7.5));
float2 edgeDistance = min(withinBrick, 1.0 - withinBrick) * float2(24.0, 7.5);
float distanceToJoint = min(edgeDistance.x, edgeDistance.y);
float antialias = max(fwidth(distanceToJoint), 0.08);
return 1.0 - smoothstep(0.35 - antialias, 0.45 + antialias, distanceToJoint);
""".strip(),
        inputs=[position_input, normal_input],
    )
    graph.link(graph.node(unreal.MaterialExpressionWorldPosition, "Brick dimensions in world cm"), mortar, "WorldPosition")
    graph.link(graph.node(unreal.MaterialExpressionVertexNormalWS, "Choose wall or top projection"), mortar, "WorldNormal")
    graph.output(graph.lerp(
        brick_color, graph.color((0.17, 0.155, 0.13), "Aged gray mortar"), mortar, "Clay bricks and mortar joints",
    ), PROPERTY.MP_BASE_COLOR)
    return graph.finish()


def _build_glass(key="glass", tint=(0.10, 0.17, 0.18)):
    graph = _Graph(key)
    graph.material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    graph.material.set_editor_property("two_sided", True)
    graph.material.set_editor_property(
        "translucency_lighting_mode", unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING,
    )
    fresnel = graph.node(unreal.MaterialExpressionFresnel, "Glass is clearer face-on", exponent=4.5, base_reflect_fraction=0.0)
    opacity = graph.lerp(
        graph.scalar(0.075, "Clear glass face opacity"),
        graph.scalar(0.28, "Glass edge opacity"), fresnel, "Angle-dependent glass opacity",
    )
    graph.output(graph.color(tint, "Restrained architectural glazing tint"), PROPERTY.MP_BASE_COLOR)
    graph.output(opacity, PROPERTY.MP_OPACITY)
    graph.output(graph.scalar(0.065, "Clean glass roughness"), PROPERTY.MP_ROUGHNESS)
    graph.output(graph.scalar(0.62, "Glass specular highlights"), PROPERTY.MP_SPECULAR)
    graph.output(graph.scalar(0.0, "Dielectric glass"), PROPERTY.MP_METALLIC)
    return graph.finish()


def _build_constant_surface(key, color, metallic, roughness, emission=0.0):
    graph = _Graph(key)
    albedo = graph.color(color, "Original fitout palette in linear color")
    graph.output(albedo, PROPERTY.MP_BASE_COLOR)
    graph.output(graph.scalar(metallic, "PBR metal fraction"), PROPERTY.MP_METALLIC)
    graph.output(graph.scalar(roughness, "PBR surface roughness"), PROPERTY.MP_ROUGHNESS)
    graph.output(graph.scalar(0.4, "Dielectric specular response"), PROPERTY.MP_SPECULAR)
    if emission > 0.0:
        glow = graph.multiply(albedo, graph.scalar(emission, "Luminaire intensity"), "Warm linear light emission")
        graph.output(glow, PROPERTY.MP_EMISSIVE_COLOR)
    return graph.finish()


def _build_dust():
    """Soft mesh-sphere dust, driven by a collision actor's game-time birth."""
    graph = _Graph("dust")
    graph.material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    graph.material.set_editor_property("two_sided", True)
    graph.material.set_editor_property(
        "translucency_lighting_mode", unreal.TranslucencyLightingMode.TLM_VOLUMETRIC_NON_DIRECTIONAL,
    )
    graph.material.set_editor_property("used_with_niagara_mesh_particles", True)
    graph.material.set_editor_property("used_with_niagara_sprites", True)
    birth = graph.parameter("BirthTime", 0.0, "Set from GetTimeSeconds when the impact dust actor begins play")
    lifetime = graph.parameter("Lifetime", 5.0, "Dust lifetime in game seconds")
    density = graph.parameter("Density", 0.32, "Maximum opacity of the dust cloud")
    time = graph.node(unreal.MaterialExpressionTime, "Game time matches GetTimeSeconds", ignore_pause=False)
    age = graph.operation(unreal.MaterialExpressionSubtract, time, birth, "Seconds since impact")
    fade_in = graph.multiply(age, graph.scalar(5.0, "0.2 second fade-in reciprocal"), "Dust impact fade-in")
    fade_in_clamped = graph.node(unreal.MaterialExpressionSaturate, "Clamp fade-in to 0..1")
    graph.link(fade_in, fade_in_clamped)
    safe_lifetime = graph.operation(
        unreal.MaterialExpressionMax, lifetime, graph.scalar(0.21, "Minimum valid lifetime"), "Prevent zero lifetime division",
    )
    remaining = graph.operation(unreal.MaterialExpressionSubtract, safe_lifetime, age, "Remaining dust lifetime")
    fade_out = graph.operation(unreal.MaterialExpressionDivide, remaining, safe_lifetime, "Dust disperses over its lifetime")
    fade_out_clamped = graph.node(unreal.MaterialExpressionSaturate, "Clamp remaining lifetime to 0..1")
    graph.link(fade_out, fade_out_clamped)
    envelope = graph.multiply(fade_in_clamped, fade_out_clamped, "Impact opacity envelope")
    fresnel = graph.node(unreal.MaterialExpressionFresnel, "Soften the dust sphere silhouette", exponent=0.8, base_reflect_fraction=0.0)
    soft_edge = graph.node(unreal.MaterialExpressionOneMinus, "Transparent sphere edges")
    graph.link(fresnel, soft_edge)
    noise = graph.noise(62.0, "Uneven world-space dust density", levels=3)
    cloud_opacity = graph.multiply(envelope, density, "Lifetime-scaled density")
    cloud_opacity = graph.multiply(cloud_opacity, soft_edge, "Soft sphere boundary")
    cloud_opacity = graph.multiply(cloud_opacity, noise, "Broken-up cloud density")
    depth_fade = graph.node(unreal.MaterialExpressionDepthFade, "Soft contact with ground and rubble", fade_distance_default=60.0)
    graph.link(cloud_opacity, depth_fade, "Opacity")
    graph.output(depth_fade, PROPERTY.MP_OPACITY)
    graph.output(graph.color((0.20, 0.175, 0.14), "Concrete dust gray-brown"), PROPERTY.MP_BASE_COLOR)
    graph.output(graph.scalar(1.0, "Fully rough dust"), PROPERTY.MP_ROUGHNESS)
    graph.output(graph.scalar(0.0, "No dust specular highlights"), PROPERTY.MP_SPECULAR)
    return graph.finish()


def _build_fracture():
    graph = _Graph("fracture")
    grain = graph.noise(0.75, "Fresh mineral aggregate grain", levels=3)
    aggregate = graph.lerp(graph.color((0.18, 0.19, 0.195), "Dark aggregate"),
                           graph.color((0.40, 0.415, 0.42), "Grey cement paste"),
                           grain, "Irregular fresh concrete interior")
    graph.output(aggregate, PROPERTY.MP_BASE_COLOR)
    graph.output(graph.scalar(0.95, "Rough broken concrete"), PROPERTY.MP_ROUGHNESS)
    graph.output(graph.scalar(0, "Nonmetallic aggregate"), PROPERTY.MP_METALLIC)
    graph.output(graph.scalar(0.22, "Mineral specular response"), PROPERTY.MP_SPECULAR)
    return graph.finish()


def build_materials():
    """Return saved, compiled Skyfall construction and original fitout materials."""
    unreal.EditorAssetLibrary.make_directory(TEXTURE_DIRECTORY)
    unreal.EditorAssetLibrary.make_directory(MATERIAL_DIRECTORY)
    textures = _import_textures()
    materials = {}
    surface_settings = {
        "concrete": ((0.77, 0.76, 0.73), 200.0, (0.73, 0.94), 200.0),
        "asphalt": ((0.84, 0.87, 0.89), 300.0, (0.81, 0.98), 18.0),
        "paint": ((0.92, 0.57, 0.055), 180.0, (0.62, 0.86), 45.0),
        "soil": ((0.30, 0.235, 0.16), 90.0, (0.89, 1.0), 18.0),
    }
    for key, settings in surface_settings.items():
        graph, _ = _textured_solid(key, textures, *settings)
        materials[key] = graph.finish()
    materials["fracture"] = _build_fracture()
    materials["metal"] = _build_rust(textures)
    materials["brick"] = _build_brick(textures)
    materials["glass"] = _build_glass()
    materials["glasstint"] = _build_glass("glasstint", (0.18, 0.155, 0.105))
    materials["dust"] = _build_dust()
    fitout_palette = {
        "aluminum": ((0.045, 0.055, 0.065), 0.8, 0.3),
        "plaster": ((0.55, 0.52, 0.47), 0.0, 0.9),
        "wood": ((0.095, 0.055, 0.028), 0.0, 0.69),
        "fabric": ((0.048, 0.067, 0.085), 0.0, 0.95),
        "bronze": ((0.22, 0.115, 0.045), 0.82, 0.36),
        "foliage": ((0.05, 0.11, 0.032), 0.0, 0.82),
        "stainless": ((0.28, 0.30, 0.32), 0.9, 0.36),
        "rubber": ((0.012, 0.015, 0.018), 0.0, 0.93),
        "linen": ((0.48, 0.45, 0.38), 0.0, 0.95),
    }
    for key, settings in fitout_palette.items():
        materials[key] = _build_constant_surface(key, *settings)
    materials["luminaire"] = _build_constant_surface("luminaire", (0.85, 0.58, 0.28), 0.0, 0.32, emission=6.0)
    if set(materials) != set(MATERIAL_NAMES):
        raise RuntimeError("The Skyfall material role set is incomplete")
    unreal.log("SKYFALL_MATERIALS_OK " + ", ".join(sorted(materials)))
    return materials


if __name__ == "__main__":
    built_materials = build_materials()
    validation = {
        "engine": unreal.SystemLibrary.get_engine_version(),
        "material_count": len(built_materials),
        "shader_errors": [],
        "materials": {},
        "source_textures": TEXTURE_SOURCES,
    }
    for role, material in built_materials.items():
        statistics = EDIT.get_statistics(material)
        validation["materials"][role] = {
            "asset": material.get_path_name(),
            "expression_count": EDIT.get_num_material_expressions(material),
            "shader_type_count": EDIT.get_num_shader_types(material),
            "pixel_shader_instructions": statistics.get_editor_property("num_pixel_shader_instructions"),
            "vertex_shader_instructions": statistics.get_editor_property("num_vertex_shader_instructions"),
        }
    validation_path = Path(__file__).resolve().parents[1] / "Saved" / "materials-validation.json"
    validation_path.write_text(json.dumps(validation, indent=2), encoding="utf-8")
    unreal.log("SKYFALL_MATERIAL_VALIDATION_OK " + str(validation_path))
