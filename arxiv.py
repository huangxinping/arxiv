#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import requests
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from lxml import etree
import re
from concurrent.futures import ThreadPoolExecutor
import pymongo
import time


class Paper(object):

    def __init__(self, id):
        chrome_options = webdriver.ChromeOptions()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument(
            '--no-sandbox')  # required when running as root user. otherwise you would get no sandbox errors.
        self.driver = webdriver.Chrome(executable_path='/usr/local/bin/chromedriver', chrome_options=chrome_options)
        self.id = id
        self.db = pymongo.MongoClient("192.168.0.125", 31345).papers

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.driver.quit()

    def save(self, url, detail):
        cursor = self.db.arxiv.find({
            'url': url
        })
        if cursor.count() <= 0:
            detail['url'] = url
            self.db.arxiv.insert_one(detail)

    def run(self, save=True):
        url = f'http://arxivdaily.com/topic/index?id={self.id}'
        if save:
            cursor = self.db.arxiv.find({'url': url})
            if cursor.count() > 0:
                print(f'    已挖掘过 {self.id}')
                return
        print(f'    开始挖掘 {self.id}')
        self.driver.get(url)
        WebDriverWait(self.driver, 300).until(
            EC.presence_of_element_located((By.XPATH, "//div/div[2]/div/main/div/article/a")))
        try:
            detail = self.dig(self.driver.page_source)
            if not save:
                print(detail)
        except Exception as e:
            with open('error.txt', 'a+') as w:
                w.writelines(f'{url}\n')
            print(f'挖掘出现异常！在 {url} {e}')
            return
        if save:
            self.save(url, detail)

    def dig(self, content):
        selector = etree.HTML(content)

        # tag
        tag = selector.xpath('//div/div[2]/div/main/div/article/a/text()')[0]

        # today paper's title
        title = selector.xpath('//div/main/div/article/div[1]/text()')[0]
        # paper's date
        result = re.search('\(.*\)', content)
        date = result.group(0).replace('(', '').replace(')', '').replace('.', '-')
        title = title.replace(result.group(0), '')

        # paper's category
        category = title.replace('每日学术速递', '')

        # abstract
        # 第一个的摘要和后续的摘要 xpath 路径不一样
        abstracts = []
        first_abstract = selector.xpath('//div/main/div/article/div//details/small/text()')
        if not first_abstract:
            first_abstract = selector.xpath('//div/main/div/article/div[2]/div/p/small[4]/text()')
        abstracts.append(first_abstract[0].replace('：', ''))
        other_abstracts = selector.xpath('//div/main/div/article/div//details')
        if other_abstracts:
            for item in other_abstracts:
                abstract = item.xpath('./text()')
                if len(abstract):
                    abstracts.append(abstract[0].replace('：', ''))
        else:
            abstracts = []
            for index in range(1, 10000):
                buffer = selector.xpath(f'//div/main/div/article/div[2]/div/p/small[{index}]')
                if not buffer:
                    break
                is_author = buffer[0].xpath('strong/text()')
                if len(is_author) and is_author[0] == '摘要':
                    for item in buffer:
                        abstracts.append(item.xpath('text()')[0].replace('：', ''))

        # attachments
        # 第一个的 PDF 和后续的 PDF xpath 路径不一样
        attachments = []
        first_pdf = selector.xpath('//div/article/div/div/p/small/a/@href')
        attachments.append(first_pdf[0])
        other_pdfs = selector.xpath('//div/article/div/div/small/a/@href')
        if not other_pdfs:
            attachments = []
            other_pdfs = selector.xpath('//div/article/div/div/p/small/a/@href')
        for item in other_pdfs:
            attachments.append(item)

        # paper title in english
        paper_english_titles = []
        for index in range(1, len(attachments) + 1):
            result = re.search(f'【{index}】.*<br><strong>', content)
            if result:
                paper_english_titles.append(result.group(0).replace('<br><strong>', '').replace(f'【{index}】 ', ''))

        # paper title in chinese
        paper_chinese_titles = []
        result = re.findall('标题</strong>：.*<br><small><strong>作者', content)
        for index in range(len(result)):
            paper_chinese_titles.append(result[index].replace('标题</strong>：', '').replace('<br><small><strong>作者', ''))

        paper_authors = []
        for index in range(1, 10000):
            buffer = selector.xpath(f'//div/main/div/article/div/div/small[{index}]')
            if not buffer:
                buffer = selector.xpath(f'//div/main/div/article/div/div/p/small[{index}]')
            if not buffer:
                break
            is_author = buffer[0].xpath('strong/text()')
            if len(is_author) and is_author[0] == '作者':
                for item in buffer:
                    paper_authors.append(item.xpath('text()')[0].replace('：', ''))

        items = [{'abstract': abstract, 'attachment': attachment, 'english_title': english_title,
                  'chinese_title': chinese_title, 'authors': author.split(',')} for
                 abstract, attachment, english_title, chinese_title, author in
                 zip(abstracts, attachments, paper_english_titles, paper_chinese_titles, paper_authors)]

        return {
            'date': date,
            'category': category,
            'tag': tag,
            'items': items
        }


class Papers(object):

    def __init__(self, category_id):
        """
        :param category_id: 类别，2-人工智能；15-计算机；9-金融；8-统计学；10-数学；11-物理学；12-生物学；13-经济学；14-电气&系统科学
        """
        self.category_id = category_id
        self.page_count = 100000
        self.executor = ThreadPoolExecutor(max_workers=10)

    def run(self, page_index=1):
        if page_index > self.page_count:
            raise Exception('超出可提供页码！')
        url = f'http://arxivdaily.com/api/threads?include=user,user.groups,firstPost,firstPost.images,category,threadVideo,question,question.beUser&filter[isSticky]=no&filter[isApproved]=1&filter[isDeleted]=no&filter[categoryId]={self.category_id}&filter[type]=&filter[isEssence]=&filter[fromUserId]=&sort=&page[number]={page_index}&page[limit]=10'
        response = requests.get(url)
        if response.ok:
            result = response.json()
            for item in result['data']:
                self.executor.submit(Paper(item['id']).run())
            self.page_count = result['meta']['pageCount']


if __name__ == '__main__':
    # Paper(2334).run(save=False)

    categories = {'2': '人工智能', '15': '计算机', '9': '金融', '8': '统计学', '10': '数学', '11': '物理学', '12': '生物学', '13': '经济学',
                  '14': '电气&系统科学'}
    max_page_count = 10000
    for category in categories.keys():
        papers = Papers(int(category))
        for page_index in range(1, max_page_count):
            print(f'正在获取：{categories[category]} 的第 {page_index} 页')
            try:
                papers.run(page_index)
            except Exception as e:
                print(e)
                break
            # time.sleep(1)
