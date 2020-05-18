# -*- coding:utf-8 -*-
import re, sys, logging, json
import pdfplumber, numpy, decimal
import pandas as pd


#大概一个英文字符的宽度
#中英文字符的高度大概是10, 有些字符跟文字不是完全等高的，
#比如逗号, 需要有容错范围。
y_tolerance = 2
x_tolerance = 5

#左右分组，最小行数
min_group_len = 5
#普通字号中英文字符大概的高度/宽度
pdf_char_width = 10
pdf_char_height = 10
pdf_line_height = pdf_char_height + 10

#有些财报里面有左右分页，单独处理
def PageInGroups(pg, word_rows):
    center = pg.width/2
    #把文本穿越中线的行去掉
    def filter_cross_center(row):
        valid = True
        for x in row:
            if x['x1']>center and x['x0']<center:
                valid = False
                break
        return valid
    rows = filter(filter_cross_center, word_rows)


    #左右分割
    left_lines = []
    right_lines = []
    for row in rows:
        left_row = []
        right_row = []
        for w in row:
            if w['x1']<center:
                left_row.append(w)
            if w['x0']>center:
                right_row.append(w)
        #print(center, len(left_row), len(right_row), row)
        if len(left_row)>0:
            left_lines.append(left_row)
        if len(right_row)>0:
            right_lines.append(right_row)

    #print("lines", len(left_lines), len(right_lines), center)

    #一个左右分割的pdf页面，必然呈现出左边页面的right边界几乎对齐
    #而右边页面的left边界也是对齐的
    #用这个方法可以避免把一些没有左右分页的页面误判为左右分页。
    if len(left_lines)>0:
        def right_edge(line):
            return line[-1]['x1']
        leftpage_rightedges = list(map(right_edge, left_lines))
        #中位数
        median_right = numpy.median(leftpage_rightedges)
        if center - median_right > pdf_char_width*2:
            #print(center, median_right, pdf_char_width)
            return [],[]

    if len(right_lines)>0:
        def left_edge(line):
            return line[0]['x0']
        rightpage_leftedges = list(map(left_edge, right_lines))
        median_left = numpy.median(rightpage_leftedges)
        if center - median_left > pdf_char_width*2:
            #print(center, median_left, pdf_char_width)
            return [], []

    return left_lines, right_lines

#有一些单元格文字过长，被折断了，尝试修复。
def merge_cross_line(rows):
    pass



def extract_tables(rows):
    #如果一个页面有多个表，暂时必须是结构一致的才能解析。否则会出错。
    #多数行都有的共同列数

    #过滤掉只有一个word的行
    def filter_dismatch_row(row):
        return len(row) > 1
    rows = list(filter(filter_dismatch_row, rows))


    #判断两行不同单元有没有重叠区域
    def overlap(cell0, cell1):
        if cell0['x0'] > cell1['x0']:
            cell0 ,cell1 = cell1, cell0

        return cell1['x0'] < cell0['x1']
            

    #判断两行是否属于同一个表。
    def similar_struct(row0 ,row1):
        #行位置差异过大, 考虑到有些文字折叠的问题，容忍度要大一点
        if abs(row0[0]['bottom'] - row1[0]['bottom'])> pdf_line_height*2:
            return False

        #表结构差异过大，判断为新的表
        if abs(len(row0) - len(row1))>1:
            return False

        #
        if len(row0) > len(row1):
            row0, row1 = row1, row0


        found = 0
        for i in range(len(row0)):
            for j in range(len(row1)):
                c0 = row0[i]
                c1 = row1[j]
                #print("c0", c0)
                #print("c1", c1)
                if overlap(c0, c1):
                    found += 1
                    #c0和c1有重叠，并且和下一个列也重叠，说明无法列对齐
                    #这必定不是同一个table
                    if j!=len(row1)-1 and overlap(c0, row1[j+1]):
                        return False
        if found == len(row0):
            return True
        else:
            return False 



    def year_row(row):
        for w in row:
            if re.search("\d{4}年", w['text']):
                return True
        return False

    def year_merged(row0, row1):
        merged = False
        for i in range(len(row0)):
            w0 = row0[i]
            for j in range(len(row1)):
                w1 = row1[j]
                if re.match('\d+月\d+日', w1['text']) or \
                    re.match('第\w+季度', w1['text']):
                    if abs(w0['x1']-w1['x1'])<x_tolerance or \
                        abs(w0['x0']-w1['x0'])<x_tolerance:
                        w1['text'] = w0['text']+w1['text']
                        row1[j] = w1
                        merged = True
        return merged, row1


    tables = []
    table = []
    for row in rows:
        if len(table)==0:
            table.append(row)
        else:
            if len(table)==1 and year_row(table[-1]):
                merged, new_row = year_merged(table[-1], row)
                #print(merged, new_row)
                if merged:
                    table[-1] = new_row
                    continue
            if similar_struct(table[-1], row):    
                table.append(row)
            else:
                if len(table)>1:
                    tables.append(table)
                table = [row]
    if len(table)>1:
        tables.append(table)


    #把具有类似结构，但缺少数据的行填充上空数据，比如财报的附注列。
    def align_table(table):
        max_fields_row = max(table, key=lambda x:len(x)).copy()
        min_fields_row = min(table, key=lambda x:len(x)).copy()
        if len(max_fields_row) == len(min_fields_row):
            return table

        #print(min_fields_row)
        #print(max_fields_row)
        #部分表结构异常，剔除掉
        if len(max_fields_row) - len(min_fields_row) > 1:
            def filter_abnormal(row):
                return len(max_fields_row) - len(row) <= 1
            table = list(filter(filter_abnormal, table))
            

        def _align(row):
            if len(row) == len(min_fields_row):
                for i in range(len(max_fields_row)):
                    found = False
                    for j in range(len(row)):
                        if overlap(max_fields_row[i], row[j]): 
                            found = True
                    if not found:
                        cell = max_fields_row[i].copy()
                        cell['text']=''
                        row.insert(i, cell)
                        return row
                #print(row)
                #print(min_fields_row)
                #print(max_fields_row)
            else:
                return row

        #print(table)
        table = list(map(_align, table))
        return table


    def get_texts(row):
        return list(map(lambda x:x['text'], row))

    def get_table_texts(table):
        assert(table)
        table = align_table(table)
        return list(map(get_texts, table))
    tables = list(map(get_table_texts, tables))

    return tables

def ExtractPageTables(pg):

    #print("pg ", pg.width, pg.height)

    #设置包围盒, 会导致page.width与坐标的关系不一致
    #bbox = (40,30,560,800)
    #pg = pg.within_bbox(bbox)


    words = pg.extract_words(x_tolerance=x_tolerance, y_tolerance=y_tolerance)
    #PingAN(words)

    #有些财报侧边有竖向文字，需要过滤掉，不然会影响表的分析.
    def filter_chars(obj):
        if obj['object_type'] == 'char':
            if obj['upright'] == 0:
                return False
            #不能在字符阶段就过滤掉，否则会在连接word的时候算错
            #if re.match('\(cid:\d+\)', obj['text']):
            #    return False
        return True

    filtered = pg.filter(filter_chars)
    words = list(filtered.extract_words(x_tolerance=x_tolerance, y_tolerance=y_tolerance))
    #有些财报使用了特别的字体，无法识别，需要过滤掉
    def filter_cid(obj):
        return not re.search('\(cid:\d+\)', obj['text'])

    words = filter(filter_cid, words)

    #def filter_long_words(obj):
    #    return obj['x1'] - obj['x0'] < pg.width/2 
    #words = filter(filter_long_words, words)

    #通过words的bottom位置信息，找出潜在的行
    words = sorted(words, key=lambda x:x['bottom'])
    rows = []
    row = []
    for word in words:
        #print(word)
        if len(row)==0:
            #fist row
            row.append(word)
        else:
            if abs(row[-1]['bottom'] - word['bottom'])<=y_tolerance:
                row.append(word)
            else:
                #row = sorted(row, key=lambda x:x['x0'])
                rows.append(row)
                row = [word]#new row
    rows.append(row)

    def sort_row(row):
        row = sorted(row, key=lambda x:x['x0'])
        return row

    rows = map(sort_row, rows)


    
    #返回的数据里面有时候有一些错误，比如前一个word的末尾与后一个word的开头重叠了
    #或者应该合并的而没有合并到一起，需要修正这些数据
    def concat_words(row):
        new_row = []
        for i in range(len(row)):
            if i!=0 and (row[i]['x0'] - row[i-1]['x1']) < x_tolerance:
                word = new_row[-1]
                word['x1'] = row[i]['x1']
                word['text'] += row[i]['text']
                new_row[-1] = word
            else:
                new_row.append(row[i])
        return new_row

    rows = list(map(concat_words, rows))


    left_groups, right_groups = PageInGroups(pg, rows)
    if len(left_groups)>0 or len(right_groups)>0:
        #print("groups", len(left_groups), len(right_groups))
        return  extract_tables(left_groups) + extract_tables(right_groups)
    else:
        return  extract_tables(rows)


def ExtractPDFtables(f):
    pdf = pdfplumber.open(f)
    tables = {}
    print("total pages:", len(pdf.pages))
    for i in range(len(pdf.pages)):
        pg = pdf.pages[i]
        #print("extract page:", i)
        new_tables = ExtractPageTables(pg)
        tables[i] = new_tables


    return tables
def ExtractPDFByPage(f, page_id):
    pdf = pdfplumber.open(f)
    pg = pdf.pages[i]
    return {i:ExtractPageTables(pg)}




#TestBonusFile()
if __name__ == "__main__":
    logging.getLogger().setLevel(logging.WARN)
    args = len(sys.argv)
    tables = {}
    if  args <= 1:
        print("require pdf pathfile...")
    elif args == 2:
        tables = ExtractPDFtables(sys.argv[1])
    else :
        page_id = int(sys.argv[2])
        tables = ExtractPDFByPage(sys.argv[1], page_id)

    #print(json.dumps(tables,indent=1,ensure_ascii=False))
