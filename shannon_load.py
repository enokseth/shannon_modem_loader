#!/bin/python3

# Samsung Shannon Modem Loader
# A lean IDA Pro loader for fancy baseband research
# Alexander Pick 2024

import idc
import idaapi
import idautils
import ida_idp
import ida_auto
import ida_bytes
import ida_nalt
import ida_name
import ida_expr
import ida_ua
import ida_funcs
import ida_struct

import struct

# this function creates debug trace structures in the database
def create_dbt_struct():

    # struct for basic DBT entries
    struct_id = idc.add_struc(0, "dbt_base", 0)
    idc.add_struc_member(struct_id, "head", -1, idaapi.FF_DWORD, -1, 4) 
    idc.add_struc_member(struct_id, "id", -1, idaapi.FF_DWORD, -1, 4) 
    idc.add_struc_member(struct_id, "type", -1, idaapi.FF_DWORD, -1, 4) 
    idc.add_struc_member(struct_id, "unknown_1", -1, idaapi.FF_DWORD, -1, 4) 
    idc.add_struc_member(struct_id, "unknown_2", -1, idaapi.FF_DWORD, -1, 4)
    idc.add_struc_member(struct_id, "unknown_3", -1, idaapi.FF_DWORD, -1, 4) 

    # DBT entries with file and string ref
    struct_id = idc.add_struc(0, "dbt_struct", 0)
    idc.add_struc_member(struct_id, "head", -1, idaapi.FF_DWORD, -1, 4) 
    idc.add_struc_member(struct_id, "id", -1, idaapi.FF_DWORD, -1, 4) 
    idc.add_struc_member(struct_id, "type", -1, idaapi.FF_DWORD, -1, 4) 
    idc.add_struc_member(struct_id, "num_param", -1, idaapi.FF_DWORD, -1, 4) 
    idc.add_struc_member(struct_id, "msg_ptr", -1, idaapi.FF_DWORD, -1, 4)
    idc.add_struc_member(struct_id, "line", -1, idaapi.FF_DWORD, -1, 4) 
    idc.add_struc_member(struct_id, "file", -1, idaapi.FF_DWORD, -1, 4) 

# This function will create DBT structs, DBT structs are debug references of various kind.
# The head contains a type byte in position 4, this indicates if a structure is a string
# ref and therefore also has a file ref or not.

def make_dbt():
    sc = idautils.Strings()

    sc.setup(strtypes=[ida_nalt.STRTYPE_C],
                  ignore_instructions=True, minlen=4)

    sc.refresh()

    for i in sc:
        if("DBT:" in str(i)):
            
            # read DBT type
            header_type = int.from_bytes(ida_bytes.get_bytes(i.ea+3, 1), "little")

            struct_name = "dbt_struct"
            if(header_type != 0x3a):
                struct_name = "dbt_base"

            #print(i.ea)
            struct_id = ida_struct.get_struc_id(struct_name)
            struct_size = ida_struct.get_struc_size(struct_id)
            #print(struct_size)

            ida_bytes.del_items(i.ea, 0,  struct_size)
            ida_bytes.create_struct(i.ea, struct_size, struct_id) 

def accept_file(fd, fname):
    fd.seek(0x0)
    try:
        image_type = fd.read(0x3)
    except UnicodeDecodeError:
        return 0

    if image_type == b"TOC":
        return {"format": "Shannon Baseband Image", "processor": "arm"}

    return 0


def load_file(fd, neflags, format):

    idaapi.set_processor_type(
        "arm:ARMv7-A&R", ida_idp.SETPROC_LOADER_NON_FATAL)

    # make sure ida understands us correctly
    idc.process_config_line("ARM_DEFAULT_ARCHITECTURE = ARMv7-A&R")
    idc.process_config_line("ARM_SIMPLIFY = NO")
    idc.process_config_line("ARM_NO_ARM_THUMB_SWITCH = NO")

    # improve auto analysis
    idc.process_config_line("ARM_REGTRACK_MAX_XREFS = 0")

    # disable Coagulate and colapse
    idc.process_config_line("ANALYSIS = 0x9bff9ff7ULL ")

    if (neflags & idaapi.NEF_RELOAD) != 0:
        return 1

    idc.msg("\nIDA Pro Shannon Modem Loader\n")
    idc.msg("https://github.com/alexander-pick/shannon_modem_loader\n\n")

    start_offset = 0x20

    while (1):

        fd.seek(start_offset)
        entry = fd.read(0x20)

        # unpack TOC entry
        toc_info = struct.unpack("12sIIIII", entry)

        seg_name = str(toc_info[0], "UTF-8").strip("\x00")

        if (seg_name == ""):
            break

        seg_start = toc_info[2]
        seg_end = toc_info[2] + toc_info[3]

        # map slices to segments
        idc.AddSeg(seg_start, seg_end, 0, 1, idaapi.saRel32Bytes, idaapi.scPub)
        if "NV" in seg_name:
            idc.set_segm_class(seg_start, "DATA")
        else:
            idc.set_segm_class(seg_start, "CODE")
        idc.set_segm_name(seg_start, seg_name+"_file")

        fd.file2base(toc_info[1], seg_start, seg_end,  0)

        # set entry points of main and bootloader
        if seg_name == "BOOT":
            idaapi.add_entry(seg_start, seg_start, "bootloader_entry", 1)
            idc.set_cmt(seg_start, "bootloader entry point", 1)
            ida_auto.auto_make_code(seg_start)

        if seg_name == "MAIN":

            # b Reset_Handler
            # b . /* 0x4  Undefined Instruction */
            # b . /* 0x8  Software Interrupt */
            # b . /* 0xC  Prefetch Abort */
            # b . /* 0x10 Data Abort */
            # b . /* 0x14 Reserved */
            # b . /* 0x18 IRQ */
            # b . /* 0x1C Reserved */

            idc.set_cmt(seg_start, "vector table", 1)

            idaapi.add_entry(seg_start, seg_start, "reset", 1)

            idaapi.add_entry(seg_start+4, seg_start+4, "undef_inst", 1)

            idaapi.add_entry(seg_start+8, seg_start+8, "soft_int", 1)

            idaapi.add_entry(seg_start+12, seg_start+12, "prefetch_abort", 1)

            idaapi.add_entry(seg_start+16, seg_start+16, "data_abort", 1)

            ida_name.set_name(seg_start+20, "reserved_1", 1)

            idaapi.add_entry(seg_start+24, seg_start+24, "irq", 1)

            ida_name.set_name(seg_start+28, "reserved_2", 1)

            ida_auto.auto_make_code(seg_start)

        start_offset += 0x20

    create_dbt_struct()
    make_dbt()

    # These 3 lines were awarded the most ugliest hack award 2024, runs a script which scheudles a callback without 
    # beeing unloaded with the loader.

    rv = ida_expr.idc_value_t()
    idc_line = 'RunPythonStatement("exec(open(\''+ idaapi.idadir("python") +'/shannon_postprocess.py\').read())")'
    ida_expr.eval_idc_expr(rv, idaapi.BADADDR, idc_line)

    idc.msg("[i] loader done\n")

    return 1
