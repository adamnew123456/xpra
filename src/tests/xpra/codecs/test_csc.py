#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2013 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import time
import binascii
from xpra.codecs.image_wrapper import ImageWrapper
from xpra.codecs.codec_constants import get_subsampling_divs

DEBUG = False
PERF_LOOP_COUNT = 16


#Some helper methods:
def check_plane(plane, data, expected):
    if expected is None:
        #nothing to check
        return
    #chop data to same size as expected sample:
    if type(data) in (buffer, str):
        data = bytearray(data)
    if type(expected) in (buffer, str):
        expected = bytearray(expected)
    actual_data = data[:len(expected)]
    if actual_data==expected:
        return  True
    assert type(actual_data)==type(expected), "expected result as %s but got %s" % (type(expected), type(actual_data))
    assert len(actual_data)==len(expected), "expected at least %s items but got %s" % (len(expected), len(actual_data))
    #now compare values, with some tolerance for rounding (off by one):
    errs = []
    for i in range(len(expected)):
        va = actual_data[i]
        ve = expected[i]
        if abs(va-ve)>3:
            errs.append(i)
    if len(errs)>0:
        print("ERROR! output differs for plane %s" % plane)
        print("check_plane(%s, .., ..) expected=%s" % (plane, dump_pixels(expected)))
        print("check_plane(%s, .., ..)   actual=%s" % (plane, dump_pixels(data)))
        print("errors at %s locations" % len(errs))
    return len(errs)==0

def dump_pixels(pixels):
    S = 64
    t = type(pixels)
    add = []
    if len(pixels)>S:
        v = pixels[:S-1]
        add = ["..."]
    else:
        v = pixels
    if t==buffer:
        l = binascii.hexlify(v) + str(add)
    elif t==bytearray:
        l = binascii.hexlify(str(v)) + str(add)
    elif t==str:
        l = binascii.hexlify(v) + str(add)
    else:
        l = [hex(x) for x in v] + str(add)
    return ("%s %s:%s" % (str(type(pixels)).ljust(20), str(len(pixels)).rjust(8), l)).replace("'","")

def hextobytes(s):
    return bytearray(binascii.unhexlify(s))



#RGB:
def test_csc_rgb(csc_module):
    print("")
    test_csc_rgb_all(csc_module)
    perf_measure_rgb(csc_module)
    perf_measure_rgb(csc_module, 512, 512)

def make_rgb_input(src_format, w, h, xratio=1, yratio=1, channelratio=64, use_strings=False):
    bpp = len(src_format)
    assert bpp==3 or bpp==4
    pixels = bytearray("\0" * (w*h*bpp))
    for y in range(h):
        for x in range(w):
            i = (y*w+x)*bpp
            v = (y*yratio*w+x*xratio)*bpp
            for j in range(3):
                pixels[i+j] = (v+j*channelratio) % 256
            if bpp==4:
                pixels[i+3] = 0
    if use_strings:
        return str(pixels)
    return pixels

def perf_measure_rgb(csc_module, w=1920, h=1080):
    rgb_src_formats = sorted([x for x in csc_module.get_input_colorspaces() if (x.find("RGB")>=0 or x.find("BGR")>=0)])
    if DEBUG:
        print("%s: rgb src_formats=%s" % (csc_module, rgb_src_formats))
    for src_format in rgb_src_formats:
        yuv_dst_formats = sorted([x for x in csc_module.get_output_colorspaces(src_format) if (x.find("RGB")<0 and x.find("BGR")<0)])
        if DEBUG:
            print("%s: yuv_formats(%s)=%s" % (csc_module, src_format, yuv_dst_formats))
        for dst_format in yuv_dst_formats:
            pixels = make_rgb_input(src_format, w, h)
            start = time.time()
            pixels = do_test_csc_rgb(csc_module, src_format, dst_format, w, h, pixels, count=PERF_LOOP_COUNT)
            end = time.time()
            if DEBUG:
                print("%s did %sx%s csc %s times in %.1fms" % (csc_module, w, h, PERF_LOOP_COUNT, end-start))
            mpps = float(w*h*PERF_LOOP_COUNT)/(end-start)
            dim = ("%sx%s" % (w,h)).rjust(10)
            info = ("%s to %s at %s" % (src_format.ljust(7), dst_format.ljust(7), dim)).ljust(40)
            print("%s: %s MPixels/s" % (info, int(mpps/1024/1024)))

def test_csc_rgb_all(csc_module):
    #some output planes we can verify:
    CHECKS = {
                ("BGRX", "YUV444P", 16, 16) : (("Y", 0, "5155585b5f6266696d7073777a7e8185888c8f9296999d"),
                                               ("U", 1, "5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a"),
                                               ("V", 2, "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1")),
                ("BGRX", "YUV422P", 32, 32) : (("Y", 0, "51585f666d737a81888f969da4aab1b87e848b9299a0a6ad343a41484f565c637d848b9299a0a7ad343a41484f565c6351585f666d737a81888f969da4aab1"),
                                               ("U", 1, "5a5a5a5a5a5a5a5a80808080cbcbcbcb80808080cbcbcbcb5b5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a80808080cbcbcbcb80808080cbcbcbcb5b5a5a5a5a5a5a"),
                                               ("V", 2, "a1a1a1a1a1a1a1a1313030308e8e8e8e303030308e8e8e8ea1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1313030308e8e8e8e303030308e8e8e8ea1a1a1a1a1a1a1")),
                ("BGRX", "YUV440P", 32, 32) : (("Y", 0, "5155585b5f6266696d7073777a7e8185888c8f9296999d"),
                                               ("U", 1, "6d6d6d6d6d6d6d6d93939393939393936d6d6d6d6d6d6d"),
                                               ("V", 2, "6868686868686868979898989898989868686868686868")),
              }
    for use_strings in (True, False):
        for w,h in ((16, 16), (32, 32)):
            rgb_in = sorted([x for x in csc_module.get_input_colorspaces() if not x.endswith("P")])
            for src_format in rgb_in:
                pixels = make_rgb_input(src_format, w, h, use_strings)
                dst_formats = sorted([x for x in csc_module.get_output_colorspaces(src_format) if x.startswith("YUV")])
                for dst_format in dst_formats:
                    checks = CHECKS.get((src_format, dst_format, w, h))
                    ok = do_test_csc_rgb(csc_module, src_format, dst_format, w, h, pixels, checks)
                    print("test_csc_rgb_all(%s) %s to %s at %sx%s use_strings=%s OK=%s" % (csc_module.get_type(), src_format, dst_format, w, h, use_strings, ok))


def do_test_csc_rgb(csc_module, src_format, dst_format, w, h, pixels, checks=(), count=1):
    ColorspaceConverterClass = getattr(csc_module, "ColorspaceConverter")
    cc = ColorspaceConverterClass()
    #print("%s()=%s" % (ColorspaceConverterClass, cc))
    cc.init_context(w, h, src_format, w, h, dst_format)
    if DEBUG:
        print("ColorspaceConverter=%s" % cc)
        print("    %s" % cc.get_info())
        print("do_test_csc_rgb() input pixels=%s" % dump_pixels(pixels))
    bpp = len(src_format)
    image = ImageWrapper(0, 0, w, h, pixels, src_format, 32, w*bpp, planes=ImageWrapper.PACKED_RGB)
    for _ in range(count):
        out = cc.convert_image(image)
    if DEBUG:
        print("do_test_csc_rgb() output=%s" % out)
    assert out.get_planes()==ImageWrapper._3_PLANES, "expected 3 planes as output but got: %s in %s" % (out.get_planes(), out)
    pixels = out.get_pixels()
    assert len(pixels)==3, "expected 3 planes but found: %s" % len(pixels)
    #for i in range(3):
    #    print("do_test_csc_rgb() plane data[%s]=%s" % (i, dump_pixels(pixels[i])))
    ok = True
    if checks:
        for plane, index, expected in checks:
            ok &= check_plane(plane, pixels[index], expected)
    return ok


#PLANAR:
def test_csc_planar(csc_module):
    print("")
    test_csc_planar_all(csc_module)
    #perf_measure_planar(ColorspaceConverter, 4096, 2048)
    perf_measure_planar(csc_module, 1920, 1080)
    perf_measure_planar(csc_module, 512, 512)

def make_planar_input(src_format, w, h, use_strings=False):
    assert src_format in ("YUV420P", "YUV422P", "YUV444P"), "invalid source format %s" % src_format
    Ydivs, Udivs, Vdivs = get_subsampling_divs(src_format)
    Yxd, Yyd = Ydivs
    Uxd, Uyd = Udivs
    Vxd, Vyd = Vdivs
    Ydata = bytearray("\0" * (w*h/Yxd/Yyd))
    Udata = bytearray("\0" * (w*h/Uxd/Uyd))
    Vdata = bytearray("\0" * (w*h/Vxd/Vyd))
    for y in range(h):
        for x in range(w):
            i = y*x
            Ydata[i/Yxd/Yyd] = i % 256
            Udata[i/Uxd/Uyd] = i % 256
            Vdata[i/Vxd/Vyd] = i % 256
    if use_strings:
        pixels = (str(Ydata), str(Udata), str(Vdata))
    else:
        pixels = (Ydata, Udata, Vdata)
    strides = (w/Yxd, w/Uxd, w/Vxd)
    return strides, pixels

def test_csc_planar_all(csc_module, w=256, h=128):
    #get_subsampling_divs()
    e420 = bytearray([0xff, 0x0, 0x87, 0x0, 0xff, 0x0, 0x88, 0x0, 0xff, 0x0, 0x81, 0x0, 0xff, 0x0, 0x82])
    e422 = bytearray([0xff, 0x0, 0x87, 0x0, 0xff, 0x0, 0x86, 0x0, 0xff, 0x0, 0x84, 0x0, 0xff, 0x0, 0x84])
    e444 = bytearray([0xff, 0x0, 0x87, 0x0, 0xff, 0x0, 0x87, 0x0, 0xff, 0x0, 0x87, 0x0, 0xff, 0x0, 0x87])
    FMT_TO_EXPECTED_OUTPUT = {("YUV420P", "XRGB")  : e420,
                              ("YUV422P", "XRGB")  : e422,
                              ("YUV444P", "XRGB")  : e444}
    for use_strings in (True, False):
        planar_in = [x for x in csc_module.get_input_colorspaces() if x.startswith("YUV")]
        for src_format in sorted(planar_in):
            strides, pixels = make_planar_input(src_format, w, h, use_strings)
            dst_formats = sorted([x for x in csc_module.get_output_colorspaces(src_format) if (not x.startswith("YUV") and not x.endswith("P"))])
            for dst_format in dst_formats:
                out_pixels = do_test_csc_planar(csc_module, src_format, dst_format, w, h, strides, pixels)
                if DEBUG:
                    print("test_csc_planar1() %s to %s head of output pixels=%s" % (src_format, dst_format, dump_pixels(out_pixels[:128])))
                expected = FMT_TO_EXPECTED_OUTPUT.get((src_format, dst_format))
                check_plane(dst_format, out_pixels, expected)

def perf_measure_planar(csc_module, w=1920, h=1080):
    for src_format in sorted(csc_module.get_input_colorspaces()):
        if src_format not in ("YUV420P", "YUV422P", "YUV444P"):
            continue
        rgb_dst_formats = sorted([x for x in csc_module.get_output_colorspaces(src_format) if (x.find("YUV")<0 and not x.endswith("P"))])
        for dst_format in rgb_dst_formats:
            strides, pixels = make_planar_input(src_format, w, h)
            #print("make_planar_input(%s, %s, %s) strides=%s, len(pixels=%s", src_format, w, h, strides, len(pixels))
            start = time.time()
            pixels = do_test_csc_planar(csc_module, src_format, dst_format, w, h, strides, pixels, count=PERF_LOOP_COUNT)
            end = time.time()
            if DEBUG:
                print("%s did %sx%s csc %s times in %.1fms" % (csc_module, w, h, PERF_LOOP_COUNT, end-start))
            mpps = float(w*h*PERF_LOOP_COUNT)/(end-start)
            dim = ("%sx%s" % (w,h)).rjust(10)
            info = ("%s to %s at %s" % (src_format.ljust(7), dst_format.ljust(7), dim)).ljust(40)
            print("%s: %s MPixels/s" % (info, int(mpps/1024/1024)))


def do_test_csc_planar(csc_module, src_format, dst_format, w, h, strides, pixels, checks=(), count=1):
    assert len(pixels)==3, "this test only handles 3-plane pixels but we have %s" % len(pixels)
    ColorspaceConverterClass = getattr(csc_module, "ColorspaceConverter")
    cc = ColorspaceConverterClass()
    #print("%s()=%s" % (ColorspaceConverterClass, cc))
    cc.init_context(w, h, src_format, w, h, dst_format)
    if DEBUG:
        print("ColorspaceConverter=%s" % cc)
        for i in range(3):
            print("test_csc() plane[%s]=%s" % (i, dump_pixels(pixels[i])))
    image = ImageWrapper(0, 0, w, h, pixels, src_format, 24, strides, planes=ImageWrapper._3_PLANES)
    for _ in range(count):
        out = cc.convert_image(image)
    if DEBUG:
        print("do_test_csc_planar() output=%s" % out)
    assert out is not None, "convert_image returned None!"
    assert out.get_planes()==ImageWrapper.PACKED_RGB, "output image %s is not in packed RGB: it has %s planes" % (out, out.get_planes())
    #clone the pixels before the wrapper falls out of scope!
    out.clone_pixel_data()
    return out.get_pixels()
