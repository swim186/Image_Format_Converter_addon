# Image Format Converter add-on
bl_info = {
    "name": "Image Format Converter",
    "author": "yejin",
    "version": (1, 4, 1),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar (N-Panel) > Format Convert",
    "description": "Batch re-encode images in a folder from one format to another",
    "category": "Import-Export",
}

import bpy
import os
import struct
import datetime

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False


# -----------------------------------------------------------------------------
# Format Maps
# -----------------------------------------------------------------------------

# 스캔할 원본 확장자 목록
SOURCE_EXT_MAP = {
    'JPG': ('.jpg', '.jpeg'),
    'PNG': ('.png',),
    'TGA': ('.tga',),
    'BMP': ('.bmp',),
    'TIFF': ('.tif', '.tiff'),
    'EXR': ('.exr',),
    'WEBP': ('.webp',),
    'ALL': ('.jpg', '.jpeg', '.png', '.tga', '.bmp', '.tif', '.tiff', '.exr', '.webp'),
}

# 변환 대상 포맷: (블렌더 file_format 값, 저장 확장자)
TARGET_FORMAT_MAP = {
    'PNG': ('PNG', '.png'),
    'JPEG': ('JPEG', '.jpg'),
    'TARGA': ('TARGA', '.tga'),
    'BMP': ('BMP', '.bmp'),
    'TIFF': ('TIFF', '.tif'),
    'OPEN_EXR': ('OPEN_EXR', '.exr'),
    'WEBP': ('WEBP', '.webp'),
}


# -----------------------------------------------------------------------------
# Scene Properties
# -----------------------------------------------------------------------------

def register_properties():
    bpy.types.Scene.imgconv_directory = bpy.props.StringProperty(
        name="Directory",
        description="변환할 이미지가 들어있는 폴더를 선택하세요",
        subtype='DIR_PATH',
        default=""
    )
    bpy.types.Scene.imgconv_source_format = bpy.props.EnumProperty(
        name="Source Format",
        description="변환할 원본 파일의 확장자를 선택하세요",
        items=[
            ('JPG', "JPG", "jpg / jpeg 파일을 변환합니다"),
            ('PNG', "PNG", "png 파일을 변환합니다"),
            ('TGA', "TGA", "tga 파일을 변환합니다"),
            ('BMP', "BMP", "bmp 파일을 변환합니다"),
            ('TIFF', "TIFF", "tif / tiff 파일을 변환합니다"),
            ('EXR', "EXR", "exr 파일을 변환합니다"),
            ('WEBP', "WEBP", "webp 파일을 변환합니다"),
            ('ALL', "ALL", "폴더 내 모든 지원 이미지 파일을 변환합니다"),
        ],
        default='JPG'
    )
    bpy.types.Scene.imgconv_target_format = bpy.props.EnumProperty(
        name="Target Format",
        description="변환할 목표 포맷을 선택하세요",
        items=[
            ('PNG', "PNG", "PNG로 재인코딩합니다"),
            ('JPEG', "JPEG", "JPEG로 재인코딩합니다"),
            ('TARGA', "TGA", "TGA로 재인코딩합니다"),
            ('BMP', "BMP", "BMP로 재인코딩합니다"),
            ('TIFF', "TIFF", "TIFF로 재인코딩합니다"),
            ('OPEN_EXR', "EXR", "OpenEXR로 재인코딩합니다"),
            ('WEBP', "WEBP", "WEBP로 재인코딩합니다"),
        ],
        default='PNG'
    )
    bpy.types.Scene.imgconv_write_log = bpy.props.BoolProperty(
        name="Metadata Log",
        description="변환한 각 파일의 카메라 정보(제조사/모델/촬영일시)를 결과 폴더에 텍스트 로그 파일로 남깁니다. Windows 탐색기에서 PNG 메타데이터가 안 보일 때 확인용으로 유용합니다",
        default=True
    )


def unregister_properties():
    del bpy.types.Scene.imgconv_directory
    del bpy.types.Scene.imgconv_source_format
    del bpy.types.Scene.imgconv_target_format
    del bpy.types.Scene.imgconv_write_log


# -----------------------------------------------------------------------------
# EXIF Orientation Helpers
# -----------------------------------------------------------------------------

def extract_jpeg_exif(filepath):
    """JPG 파일에서 (orientation, raw_tiff_bytes)를 추출합니다.
    EXIF가 없으면 (1, None)을 반환합니다."""
    try:
        with open(filepath, 'rb') as f:
            data = f.read(1048576)  # 썸네일 포함 EXIF 대비 최대 1MB까지 스캔

        if data[0:2] != b'\xff\xd8':
            return 1, None

        i = 2
        while i + 4 <= len(data):
            if data[i] != 0xFF:
                break
            marker = data[i + 1]
            if marker == 0xE1:  # APP1 (EXIF) 마커
                seg_len = struct.unpack('>H', data[i + 2:i + 4])[0]
                exif_data = data[i + 4:i + 2 + seg_len]
                if exif_data[:6] == b'Exif\x00\x00':
                    tiff = exif_data[6:]
                    orientation = _parse_tiff_orientation(tiff)
                    return orientation, tiff
                return 1, None
            elif marker in (0xD8, 0xD9, 0xDA):
                break
            else:
                seg_len = struct.unpack('>H', data[i + 2:i + 4])[0]
                i += 2 + seg_len
        return 1, None
    except Exception:
        return 1, None


def _parse_tiff_orientation(tiff):
    if len(tiff) < 8:
        return 1
    byte_order = tiff[0:2]
    if byte_order == b'II':
        endian = '<'
    elif byte_order == b'MM':
        endian = '>'
    else:
        return 1

    try:
        ifd_offset = struct.unpack(endian + 'I', tiff[4:8])[0]
        num_entries = struct.unpack(endian + 'H', tiff[ifd_offset:ifd_offset + 2])[0]
        entry_start = ifd_offset + 2
        for i in range(num_entries):
            entry = tiff[entry_start + i * 12: entry_start + i * 12 + 12]
            if len(entry) < 12:
                break
            tag = struct.unpack(endian + 'H', entry[0:2])[0]
            if tag == 0x0112:  # Orientation 태그
                return struct.unpack(endian + 'H', entry[8:10])[0]
        return 1
    except Exception:
        return 1


def apply_exif_orientation(pixels, orientation):
    """numpy 배열(height, width, channels)에 EXIF 방향 값을 적용해 회전/반전시킵니다."""
    if orientation == 2:
        return np.fliplr(pixels)
    elif orientation == 3:
        return np.rot90(pixels, 2)
    elif orientation == 4:
        return np.flipud(pixels)
    elif orientation == 5:
        return np.fliplr(np.rot90(pixels, 1))
    elif orientation == 6:
        return np.rot90(pixels, -1)
    elif orientation == 7:
        return np.fliplr(np.rot90(pixels, -1))
    elif orientation == 8:
        return np.rot90(pixels, 1)
    return pixels


def get_ifd0_ascii_tag(tiff, tag_id):
    """TIFF(EXIF) IFD0에서 지정한 태그의 ASCII 문자열 값을 읽어옵니다."""
    if len(tiff) < 8:
        return None
    byte_order = tiff[0:2]
    if byte_order == b'II':
        endian = '<'
    elif byte_order == b'MM':
        endian = '>'
    else:
        return None

    try:
        ifd_offset = struct.unpack(endian + 'I', tiff[4:8])[0]
        num_entries = struct.unpack(endian + 'H', tiff[ifd_offset:ifd_offset + 2])[0]
        entry_start = ifd_offset + 2
        for i in range(num_entries):
            pos = entry_start + i * 12
            entry = tiff[pos:pos + 12]
            if len(entry) < 12:
                break
            tag = struct.unpack(endian + 'H', entry[0:2])[0]
            if tag != tag_id:
                continue
            typ = struct.unpack(endian + 'H', entry[2:4])[0]
            count = struct.unpack(endian + 'I', entry[4:8])[0]
            if typ != 2:  # ASCII 타입이 아니면 무시
                return None
            if count <= 4:
                raw = entry[8:8 + count]
            else:
                offset = struct.unpack(endian + 'I', entry[8:12])[0]
                raw = tiff[offset:offset + count]
            return raw.split(b'\x00')[0].decode('ascii', errors='ignore').strip()
        return None
    except Exception:
        return None


_TIFF_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


def _parse_ifd(tiff, offset, endian):
    """지정 오프셋의 IFD를 {tag: (type, count, raw_bytes)} 형태로 파싱합니다."""
    entries = {}
    try:
        num_entries = struct.unpack(endian + 'H', tiff[offset:offset + 2])[0]
        start = offset + 2
        for i in range(num_entries):
            pos = start + i * 12
            e = tiff[pos:pos + 12]
            if len(e) < 12:
                break
            tag = struct.unpack(endian + 'H', e[0:2])[0]
            typ = struct.unpack(endian + 'H', e[2:4])[0]
            count = struct.unpack(endian + 'I', e[4:8])[0]
            size = _TIFF_TYPE_SIZES.get(typ, 1) * count
            if size <= 4:
                raw = e[8:8 + size]
            else:
                off = struct.unpack(endian + 'I', e[8:12])[0]
                raw = tiff[off:off + size]
            entries[tag] = (typ, count, raw)
    except Exception:
        pass
    return entries


def _decode_entry(endian, entry, as_fraction=False):
    """IFD 엔트리를 실제 값으로 변환합니다. as_fraction=True면 RATIONAL을 (분자,분모)로 반환."""
    if entry is None:
        return None
    typ, count, raw = entry
    try:
        if typ == 2:  # ASCII
            return raw.split(b'\x00')[0].decode('ascii', errors='ignore').strip()
        elif typ == 3:  # SHORT
            vals = struct.unpack(endian + f'{count}H', raw[:count * 2])
            return vals[0] if count == 1 else list(vals)
        elif typ == 4:  # LONG
            vals = struct.unpack(endian + f'{count}I', raw[:count * 4])
            return vals[0] if count == 1 else list(vals)
        elif typ in (5, 10):  # RATIONAL / SRATIONAL
            fmt = 'II' if typ == 5 else 'ii'
            results = []
            for i in range(count):
                n, d = struct.unpack(endian + fmt, raw[i * 8:i * 8 + 8])
                if as_fraction:
                    results.append((n, d))
                else:
                    results.append(n / d if d else 0.0)
            return results[0] if count == 1 else results
    except Exception:
        return None
    return None


def _fmt_num(x):
    """정수면 소수점 없이, 아니면 소수점 둘째자리까지 (끝의 0은 제거)."""
    if x is None:
        return None
    if isinstance(x, float):
        if x == int(x):
            return str(int(x))
        return f"{x:.2f}".rstrip('0').rstrip('.')
    return str(x)


def get_camera_details(tiff):
    """IFD0 + Exif SubIFD에서 카메라 세부 정보를 추출합니다."""
    details = {}
    if len(tiff) < 8:
        return details
    byte_order = tiff[0:2]
    if byte_order == b'II':
        endian = '<'
    elif byte_order == b'MM':
        endian = '>'
    else:
        return details

    try:
        ifd0_offset = struct.unpack(endian + 'I', tiff[4:8])[0]
        ifd0 = _parse_ifd(tiff, ifd0_offset, endian)

        details['make'] = _decode_entry(endian, ifd0.get(0x010F))
        details['model'] = _decode_entry(endian, ifd0.get(0x0110))
        details['datetime'] = _decode_entry(endian, ifd0.get(0x0132))

        exif_ptr_entry = ifd0.get(0x8769)  # Exif SubIFD 포인터
        if exif_ptr_entry:
            sub_offset = _decode_entry(endian, exif_ptr_entry)
            if isinstance(sub_offset, int):
                sub_ifd = _parse_ifd(tiff, sub_offset, endian)

                details['fnumber'] = _decode_entry(endian, sub_ifd.get(0x829D))          # F-스톱
                exposure = _decode_entry(endian, sub_ifd.get(0x829A), as_fraction=True)   # 노출 시간
                details['exposure'] = exposure
                details['focal_length'] = _decode_entry(endian, sub_ifd.get(0x920A))     # 초점 거리
                details['max_aperture'] = _decode_entry(endian, sub_ifd.get(0x9202))      # 조리개 최대 개방
                details['flash'] = _decode_entry(endian, sub_ifd.get(0x9209))             # 플래시
                details['focal_length_35mm'] = _decode_entry(endian, sub_ifd.get(0xA405))  # 35mm 초점 거리
    except Exception:
        pass
    return details


def format_camera_log(details):
    """카메라 세부 정보를 사람이 읽을 수 있는 한 줄 문자열로 만듭니다."""
    parts = []
    camera = " ".join(p for p in (details.get('make'), details.get('model')) if p).strip()
    if camera:
        parts.append(f"Camera: {camera}")

    fnumber = _fmt_num(details.get('fnumber'))
    if fnumber:
        parts.append(f"F/{fnumber}")

    exposure = details.get('exposure')
    if exposure and isinstance(exposure, tuple) and exposure[1]:
        n, d = exposure
        if n == 1:
            parts.append(f"노출시간 1/{d}초")
        else:
            parts.append(f"노출시간 {n}/{d}초")

    focal = _fmt_num(details.get('focal_length'))
    if focal:
        parts.append(f"초점거리 {focal}mm")

    aperture = _fmt_num(details.get('max_aperture'))
    if aperture:
        parts.append(f"조리개 {aperture}")

    flash = details.get('flash')
    if flash is not None:
        parts.append("플래시켬" if (flash & 1) else "플래시끔")

    focal35 = details.get('focal_length_35mm')
    if focal35 is not None:
        parts.append(f"35mm 초점거리 {focal35}")

    return " | ".join(parts)


# -----------------------------------------------------------------------------
# Operator
# -----------------------------------------------------------------------------

class IMAGE_OT_batch_convert_format(bpy.types.Operator):
    bl_idname = "image.batch_convert_format"
    bl_label = "Convert"
    bl_description = "지정한 폴더의 이미지를 실제로 재인코딩하여 선택한 포맷으로 새로 저장합니다"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene

        valid_exts = SOURCE_EXT_MAP[scene.imgconv_source_format]
        target_key = scene.imgconv_target_format
        blender_format, target_ext = TARGET_FORMAT_MAP[target_key]

        directory = bpy.path.abspath(scene.imgconv_directory)

        if not directory or not os.path.isdir(directory):
            self.report({'ERROR'}, "유효한 폴더 경로를 선택해주세요.")
            return {'CANCELLED'}

        # 타겟 포맷 이름으로 된 새 하위 폴더 생성 (예: PNG, JPG, TGA ...)
        output_dir = os.path.join(directory, target_ext.lstrip('.').upper())
        os.makedirs(output_dir, exist_ok=True)

        count = 0
        failed = []
        log_lines = []

        for filename in sorted(os.listdir(directory)):
            name, ext = os.path.splitext(filename)
            if ext.lower() not in valid_exts:
                continue

            filepath = os.path.join(directory, filename)

            if filename in bpy.data.images:
                bpy.data.images.remove(bpy.data.images[filename])

            try:
                img = bpy.data.images.load(filepath, check_existing=False)
            except RuntimeError as e:
                failed.append(filename)
                print(f"[FAILED] {filename}: {e}")
                continue

            # 로드는 됐지만 실제 픽셀 데이터가 없는 손상/미지원 인코딩 파일 방지
            if img.size[0] == 0 or img.size[1] == 0:
                failed.append(filename)
                print(f"[FAILED] {filename}: 이미지 데이터를 읽을 수 없습니다 (손상되었거나 지원되지 않는 인코딩)")
                bpy.data.images.remove(img)
                continue

            new_filepath = os.path.join(output_dir, name + target_ext)

            # JPG의 EXIF 데이터(방향 태그 + 원본 메타데이터)를 확인
            orientation = 1
            tiff_bytes = None
            if ext.lower() in ('.jpg', '.jpeg'):
                orientation, tiff_bytes = extract_jpeg_exif(filepath)

            if orientation != 1 and NUMPY_AVAILABLE:
                width, height = img.size
                channels = img.channels
                pixels = np.array(img.pixels[:], dtype=np.float32).reshape((height, width, channels))

                # 블렌더 픽셀 배열은 bottom-up(아래->위) 순서라 표준 top-down 기준으로 변환 후 보정
                pixels = np.flipud(pixels)
                pixels = apply_exif_orientation(pixels, orientation)
                pixels = np.flipud(pixels)  # 다시 블렌더 bottom-up 순서로 되돌림

                new_h, new_w = pixels.shape[0], pixels.shape[1]

                out_img = bpy.data.images.new(
                    name="__imgconv_tmp__",
                    width=new_w,
                    height=new_h,
                    alpha=(channels == 4)
                )
                out_img.pixels = pixels.ravel().tolist()
                out_img.file_format = blender_format
                out_img.filepath_raw = new_filepath

                bpy.data.images.remove(img)
                img = out_img
            else:
                img.file_format = blender_format
                img.filepath_raw = new_filepath

            try:
                img.save()
            except RuntimeError as e:
                failed.append(filename)
                print(f"[FAILED] {filename}: {e}")
                bpy.data.images.remove(img)
                continue

            bpy.data.images.remove(img)

            # Windows 탐색기에서 안 보일 수 있는 카메라 정보를 텍스트 로그로 남김
            if scene.imgconv_write_log:
                if tiff_bytes is not None:
                    details = get_camera_details(tiff_bytes)
                    camera_info = format_camera_log(details)
                    if camera_info:
                        log_lines.append(f"{filename} -> {os.path.basename(new_filepath)} | {camera_info}")
                    else:
                        log_lines.append(f"{filename} -> {os.path.basename(new_filepath)} | (카메라 정보 없음)")
                else:
                    log_lines.append(f"{filename} -> {os.path.basename(new_filepath)} | (EXIF 없음)")

            count += 1
            print(f"Converted: {filename} -> {os.path.basename(new_filepath)}")

        # 실패한 파일도 로그에 함께 기록
        if scene.imgconv_write_log:
            for f in failed:
                log_lines.append(f"{f} | (변환 실패)")

        if scene.imgconv_write_log and log_lines:
            log_path = os.path.join(output_dir, "metadata_log.txt")
            try:
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(f"\n===== 변환 실행: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====\n")
                    f.write("\n".join(log_lines) + "\n")
                print(f"[LOG] 메타데이터 로그 저장: {log_path}")
            except Exception as e:
                print(f"[LOG] 로그 저장 실패: {e}")

        msg = f"Converted {count} file(s) to {target_key} in '{os.path.basename(output_dir)}' folder."
        if scene.imgconv_write_log and log_lines:
            msg += " (metadata_log.txt saved)"
        if failed:
            msg += f" Failed: {len(failed)} ({', '.join(failed)})"
        self.report({'INFO'} if not failed else {'WARNING'}, msg)
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Panel
# -----------------------------------------------------------------------------

class VIEW3D_PT_image_format_converter(bpy.types.Panel):
    bl_label = "Format Convert"
    bl_idname = "VIEW3D_PT_image_format_converter"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Format Convert"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        col = layout.column(align=True)
        col.label(text="Source Folder")
        col.prop(scene, "imgconv_directory", text="")

        col.separator()
        col.label(text="Source Format")
        col.prop(scene, "imgconv_source_format", text="")

        col.separator()
        col.label(text="Target Format")
        col.prop(scene, "imgconv_target_format", text="")

        col.separator()
        col.prop(scene, "imgconv_write_log")

        col.separator()
        col.operator("image.batch_convert_format", icon='IMAGE_DATA')


# -----------------------------------------------------------------------------
# Register
# -----------------------------------------------------------------------------

classes = (
    IMAGE_OT_batch_convert_format,
    VIEW3D_PT_image_format_converter,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    register_properties()
    print(f"[Image Format Converter] v{bl_info['version']} registered (EXIF fix build)")


def unregister():
    unregister_properties()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
