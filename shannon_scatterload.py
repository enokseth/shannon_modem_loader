#!/bin/python3

# Samsung Shannon Modem Loader - Scatter Processor
# This script is autoamtically executed by the loader
# Alexander Pick 2024-2025

import idc
import idaapi
import ida_ua
import ida_idp
import ida_bytes
import idautils
import ida_name
import ida_funcs
import ida_auto

import shannon_generic
import shannon_structs

import os

# process the scatter load function
def process_scatterload(reset_func_cur):

    scatter_target = next(idautils.CodeRefsFrom(reset_func_cur, 0))

    scatterload = idc.get_operand_value(scatter_target, 0)

    if ((not scatterload) or (scatterload < 0xFFFF)):
        idc.msg("[e] scatter table not found\n")
        return None

    idc.msg("[i] scatterload(): %x\n" % (scatterload))

    ida_name.set_name(scatterload, "scatterload", ida_name.SN_NOCHECK)

    return scatterload

# recreate function at the given offset, i.e. if something was re-aligned around it
# this has to be done sometimes in RT optimized code to get proper results
def recreate_function(op):

    ida_funcs.del_func(op)
    ida_bytes.del_items(op)
    idc.create_insn(op)
    ida_funcs.add_func(op)
    func_o = ida_funcs.get_func(op)

    if func_o is not None:
        ida_funcs.reanalyze_function(func_o)
        ida_auto.auto_wait()

# create the scatter table
def create_scatter_tbl(scatterload):

    recreate_function(scatterload)

    scatter_tbl = idc.get_operand_value(scatterload, 1)

    start_bytes = ida_bytes.get_bytes(scatter_tbl, 4)
    stop_bytes = ida_bytes.get_bytes((scatter_tbl + 4), 4)

    if (start_bytes == None or stop_bytes == None):
        idc.msg("[e] unable to create scatter table\n")
        return

    scatter_start = int.from_bytes(start_bytes, "little")
    scatter_stop = int.from_bytes(stop_bytes, "little")

    scatter_start = (scatter_start + scatter_tbl) & 0xFFFFFFFF
    scatter_stop = (scatter_stop + scatter_tbl) & 0xFFFFFFFF
    scatter_size = scatter_stop - scatter_start

    struct_id = idc.get_struc_id("scatter")
    struct_size = idc.get_struc_size(struct_id)

    idc.msg("[i] scatter table at %x, size %d, table has %d entries\n" %
            (scatter_start, scatter_size, scatter_size / struct_size))

    ida_name.set_name(scatter_start, "scatter_tbl", ida_name.SN_NOCHECK)

    tbl = read_scattertbl(scatter_start, scatter_size)

    op_list = []

    # first round of processing, define ops (these are the functions which process the scatter data)
    for entry in tbl:

        op = entry[3]
        # realign if we are off by one here due to thumb and stuff
        if (op % 4):
            op += 1

        recreate_function(op)

        op_list.append(op)

    # make a "unique" list by converting it to a set and back
    op_list = list(set(op_list))

    ops = find_scatter_functions(op_list)
    process_scattertbl(scatter_start, scatter_size, ops)

# find the scatter functions in database
def find_scatter_functions(op_list):

    # possible scatter ops
    scatter_null = None
    scatter_zero = None
    scatter_copy = None
    scatter_comp = None

    # I am aware that there are some patterns and stuff to identify these which originate in basespec research
    # by KAIST. At this point we already have a very small amout of candidates, decompression algorithms use
    # multiple loops, zeroinit will zero a couple of regs, and cpy will loop to itself. This is easy enough to
    # tell the scatterload functions apart using metrics instead of a pattern.

    for op in op_list:

        # get boundaries of function
        idc.msg("[i] processing scatter function at %x\n" % op)

        found = False

        # process functions

        #recreate_function(op)

        metrics = shannon_generic.get_metric(op)

        # shannon_generic.print_metrics(op, metrics)

        scatter_func_offset = op

        opcode = ida_ua.ua_mnem(scatter_func_offset)
        if (opcode == "MOVS"):
            scatter_func_offset = idc.next_head(scatter_func_offset)
            opcode = ida_ua.ua_mnem(scatter_func_offset)
            if (opcode == "MOVS"):
                # we found zero init
                ida_name.set_name(op, "scatterload_zeroinit",
                                  ida_name.SN_NOCHECK | ida_name.SN_FORCE)

                found = True

                if (scatter_zero != None):
                    idc.msg("[e] scatterload_zeroinit() found at %x, already found at %x before\n" % (
                        op, scatter_zero))
                else:
                    scatter_zero = op
                    idc.msg("[i] found scatterload_zeroinit() at %x\n" % op)
                continue

        for branch in metrics[0]:

            operand = idc.get_operand_value(branch, 0)

            if (operand == op):
                # we found a loop to the first inst, this is copy
                ida_name.set_name(op, "scatterload_copy",
                                  ida_name.SN_NOCHECK | ida_name.SN_FORCE)

                found = True

                if (scatter_copy != None):
                    idc.msg("[e] scatterload_copy found() at %x, already found at %x before\n" % (
                        op, scatter_copy))
                else:
                    scatter_copy = op
                    idc.msg("[i] found scatterload_copy() at %x\n" % op)
                break

        if ((len(metrics[0]) >= 3) and (found == False)):

            # decompression requires multiple loops
            ida_name.set_name(op, "scatterload_decompress",
                              ida_name.SN_NOCHECK | ida_name.SN_FORCE)

            found = True

            if (scatter_comp != None):
                idc.msg("[e] scatterload_decompress() found at %x, already found at %x before\n" % (
                    op, scatter_comp))
            else:
                scatter_comp = op
                idc.msg("[i] found scatterload_decompress() at %x\n" % op)
            continue

        # if it's nothing of the above, it is null
        if (found == False):
            ida_name.set_name(op, "scatterload_null",
                              ida_name.SN_NOCHECK | ida_name.SN_FORCE)
            scatter_null = op

    return [scatter_null, scatter_zero, scatter_copy, scatter_comp]

# scatter struct
# 0 - src
# 1 - dst
# 2 - size
# 3 - op

#process the scatter table
def process_scattertbl(scatter_start, scatter_size, ops):

    tbl = read_scattertbl(scatter_start, scatter_size)

    scatter_id = 0

    for entry in tbl:

        idc.msg("[i] processing scatter - src:%x dst: %x size: %d op: %x\n" %
                (entry[0], entry[1], entry[2], entry[3]))

        index = 0
        for op in ops:
            # check if the requested op matches a known function offset
            if (entry[3] == op):
                # if it does, at which index of the op list?
                match index:
                    case 0:
                        # shannon_generic.DEBUG("[d] scatter_null\n")

                        # just adding these won't invaldiate any data in it, but allows us to see what
                        # was supposed to be mapped or zerored out
                        if (entry[2] > 0):
                            shannon_generic.add_memory_segment(entry[1], entry[2],
                                                               "SCATTERNULL_" + str(scatter_id),
                                                               "CODE", False)
                    case 1:
                        # shannon_generic.DEBUG("[d] scatter_zero\n")

                        if (entry[2] > 0):
                            shannon_generic.add_memory_segment(entry[1], entry[2],
                                                               "SCATTERZERO_" + str(scatter_id),
                                                               "CODE", False)
                    case 2:

                        # shannon_generic.DEBUG("[d] scatter_copy\n")
                        # copy in idb

                        if (entry[2] > 0):

                            # create a new segment for the scatter and copy bytes over
                            shannon_generic.add_memory_segment(entry[1], entry[2],
                                                               "SCATTER_" + str(scatter_id),
                                                               "CODE", False)

                            shannon_generic.DEBUG("[d] src: %x cnt: %d dst: %x " %
                                                  (entry[0], entry[2], entry[1]))

                            chunk = ida_bytes.get_bytes(entry[0], entry[2])

                            shannon_generic.DEBUG("len: %s\n" % (len(chunk)))

                            ida_bytes.put_bytes(entry[1], chunk)

                    case 3:  # decpmpression

                        chunk = scatterload_decompress(entry[0], entry[2])

                        shannon_generic.add_memory_segment(entry[1], len(chunk),
                                                           "SCATCOMP_" + str(scatter_id),
                                                           "CODE", False)

                        idaapi.patch_bytes(entry[1], chunk)

                        idc.msg("[i] decompressed %d bytes, from %x to %x\n"
                                % (len(chunk), entry[0], entry[1]))

            index += 1
        scatter_id += 1

# read and pre-process the scatter table
def read_scattertbl(scatter_start, scatter_size):

    struct_id = idc.get_struc_id("scatter")
    struct_size = idc.get_struc_size(struct_id)

    tbl = []

    scatter_offset = scatter_start

    sptr = shannon_structs.get_struct(struct_id)
    src_ptr = shannon_structs.get_offset_by_name(sptr, "src")
    dst_ptr = shannon_structs.get_offset_by_name(sptr, "dst")
    size_ptr = shannon_structs.get_offset_by_name(sptr, "size")
    op_ptr = shannon_structs.get_offset_by_name(sptr, "op")

    while (scatter_offset < (scatter_start + scatter_size)):

        entry = []

        ida_bytes.del_items(scatter_offset, 0, struct_size)
        ida_bytes.create_struct(scatter_offset, struct_size, struct_id)

        entry.append(int.from_bytes(ida_bytes.get_bytes(
            (scatter_offset + src_ptr), 4), "little"))

        entry.append(int.from_bytes(ida_bytes.get_bytes(
            (scatter_offset + dst_ptr), 4), "little"))

        entry.append(int.from_bytes(ida_bytes.get_bytes(
            (scatter_offset + size_ptr), 4), "little"))

        entry.append(int.from_bytes(ida_bytes.get_bytes(
            (scatter_offset + op_ptr), 4), "little"))

        tbl.append(entry)

        scatter_offset += struct_size

    return tbl

# find scatter related code
def find_scatter():

    idc.msg("[i] trying to find scatter functions\n")

    mode_switch = 0

    reset_vector_offset = idc.get_name_ea_simple("reset_v")

    # get boundaries of function
    reset_func_start = idc.get_func_attr(reset_vector_offset, idc.FUNCATTR_START)
    reset_func_end = idc.get_func_attr(reset_vector_offset, idc.FUNCATTR_END)

    if (reset_func_start != idaapi.BADADDR and reset_func_end != idaapi.BADADDR):

        func_cur = reset_func_start

        while (1):
            func_cur = idc.next_head(func_cur)
            opcode = ida_ua.ua_mnem(func_cur)

            # bailout
            if (opcode == None):
                continue

            if ("MSR" in opcode):
                cpsr = idc.get_operand_value(func_cur, 0)
                cpsr_value = idc.get_operand_value(func_cur, 1)

                if (cpsr == -1):
                    continue

                cpsr_str = ida_idp.get_reg_name(cpsr, 0)

                if ("CPSR" in cpsr_str):
                    if (cpsr_value == 0xD3):

                        if (mode_switch == 0):
                            mode_switch += 1
                            continue

                        #shannon_generic.DEBUG("[d] second supervisor mode switch found: %x\n" % func_cur)

                        reset_func_cur = func_cur

                        while (1):
                            reset_func_cur = idc.next_head(reset_func_cur)
                            reset_opcode = ida_ua.ua_mnem(reset_func_cur)

                            # bailout
                            if (reset_opcode == None):
                                idc.msg("[e] no reset_opcode\n")
                                return

                            # scatterload is the first branch in main, right after the crt (reset vector)
                            if ("B" == reset_opcode):

                                shannon_generic.DEBUG(
                                    "[d] scatter candidate at %x\n" % reset_func_cur)

                                # it's all about beeing flexible ...
                                b_target = idc.get_operand_value(reset_func_cur, 0)

                                next_opcode = ida_ua.ua_mnem(b_target)
                                if (next_opcode == None):
                                    # shannon_generic.DEBUG("[d] error in scatter branch\n")
                                    return

                                if ("B" == next_opcode):
                                    shannon_generic.DEBUG(
                                        "[d] additional jump at %x\n" % b_target)
                                    # new BB jump twice, so we check that here and follow the white rabbit
                                    reset_func_cur = b_target

                                scatterload = process_scatterload(reset_func_cur)

                                if (scatterload):
                                    create_scatter_tbl(scatterload)

                                return

                            # abort if nothing was found
                            if (reset_func_cur >= reset_func_end):
                                return

            if (func_cur >= reset_func_end):
                return

# decompressions are always "fun" to re, thanks roxfan for a hint to fix an
# anoying error in this

# after reversing it I think it is LZ77 which is also one of the compressions
# the ARM linker supports next to RLE

# decompress from src to dst, input buffer cnt - costed me an arm and a leg to
# get it working but it now uncompresses 100% of the buffer and does it correctly
def scatterload_decompress(src, cnt):

    src_index = 0
    dst_index = 0

    output_buffer = bytearray(cnt)

    while (src_index < cnt):

        # read the current byte from the source and increment the source index
        cur_byte = ida_bytes.get_byte(src + src_index)
        src_index += 1

        # extract the lower 2 bits of the current byte
        cpy_bytes = cur_byte & 3

        # if cpy_bytes is 0, read the next byte from the source
        if (cpy_bytes == 0):
            cpy_bytes = ida_bytes.get_byte(src + src_index)
            src_index += 1

        # extract the upper 4 bits of the current byte to get 'high_4_b'
        high_4_b = cur_byte >> 4

        # if 'high_4_b' is 0, read the next byte from the source
        if (high_4_b == 0):
            high_4_b = ida_bytes.get_byte(src + src_index)
            src_index += 1

        # copy x bytes from the source to the destination
        for _ in range(cpy_bytes):

            if (dst_index >= cnt):
                #shannon_generic.DEBUG("[d] out of bound write to %x/%x\n" % (cnt, dst_index))
                return bytes(list(output_buffer))

            if (dst_index >= len(output_buffer)):
                output_buffer = output_buffer.ljust(dst_index + 1, b"\x00")

            # copy byte from source to destination
            byte = ida_bytes.get_byte(src + src_index)
            output_buffer[dst_index] = byte

            dst_index += 1
            src_index += 1

        # if 'high_4_b' is non-zero, perform additional operations
        if (high_4_b):

            # read the offset byte from the source and increment the source index
            offset = ida_bytes.get_byte(src + src_index)
            src_index += 1

            # extract bits 2 and 3 from the current byte
            bit_2_3 = cur_byte & 0xC

            # calculate the source pointer for backward copy
            src_ptr = dst_index - offset

            # if both are set 0x12 == b1100
            if (bit_2_3 == 0xC):

                # if bits set, get one more byte
                bit_2_3 = ida_bytes.get_byte(src + src_index)
                src_index += 1
                src_ptr -= 256 * bit_2_3

            else:
                # otherwise, adjust the source pointer based on extracted bits
                src_ptr -= 64 * bit_2_3

            # copy 'high_4_b + 1' bytes from the previously decompressed data
            for _ in range(high_4_b + 1):

                # buffer max bailout
                if (dst_index >= cnt):
                    #shannon_generic.DEBUG("[d] out of bound write to %x/%x/%x\n" % (cnt, dst_index, src_index))
                    return bytes(list(output_buffer))

                if (dst_index >= len(output_buffer)):
                    output_buffer = output_buffer.ljust(dst_index + 1, b"\x00")

                # some possible error conditions
                if (src_ptr > cnt):
                    #shannon_generic.DEBUG("[d] out of bound read to %x/%x/%x\n" % (cnt, src_ptr, src_index))
                    return bytes(list(output_buffer))

                if (src_ptr < 0):
                    #shannon_generic.DEBUG("[d] negativ read %x/%x\n" % (src_ptr, src_index))
                    return bytes(list(output_buffer))

                # copy byte from previously decompressed data to the current destination
                output_buffer[dst_index] = output_buffer[src_ptr]

                dst_index += 1
                src_ptr += 1

    # return the final source index after decompression
    return bytes(list(output_buffer))


# for debugging purpose export SHANNON_WORKFLOW="NO"
if (os.environ.get('SHANNON_WORKFLOW') == "NO"):
    idc.msg("[i] running scatter load in standalone mode")
    find_scatter()
