import os
from typing import TypedDict, List
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

load_dotenv()

class State(TypedDict):
    text: str 
    classification: str
    entities: List[str]
    summary: str

llm = ChatOpenAI(
    model="gpt-5.6-luna",
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_API_URL"),
    temperature=0
)

def classification_node(state: State):
    """将文本归类到以下类别之一：新闻、博客、研究、其他"""
    prompt = PromptTemplate(
        input_variables=["text"],
        template="将以下文本归类到以下类别之一：新闻、博客、研究、其他\n\n文本：{text}\n\n分类："
    )

    message = HumanMessage(
        content=prompt.format(text=state["text"])
    )

    classification = llm.invoke([message]).content.strip()

    return {"classification":classification}


def entity_extraction_node(state: State):
    """从文本中提取所有实体（人物、组织、地点）"""
    prompt = PromptTemplate(
        input_variables=["text"],
        template="从以下文本中提取所有的实体（人名、组织、地点）\n\n文本：{text}\n\n实体："
    )

    message = HumanMessage(
        content=prompt.format(text=state["text"])
    )

    entities = llm.invoke([message]).content.strip().split("\n")

    return {"entities":entities}

def summarization_node(state: State):
    ''' 用一个短句总结该文本 '''
    prompt = PromptTemplate(
        input_variables=["text"],
        template="将以下文本用一个短句进行总结。\n\n文本：{text}\n\n总结："
    )
    message = HumanMessage(content=prompt.format(text=state["text"]))
    summary = llm.invoke([message]).content.strip()
    return {"summary": summary}


workflow = StateGraph(State)

# 向图中添加节点
workflow.add_node("classification_node", classification_node)
workflow.add_node("entity_extraction", entity_extraction_node)
workflow.add_node("summarization", summarization_node)

# 向图中添加边
workflow.set_entry_point("classification_node") # 设置图的入口点
workflow.add_edge("classification_node", "entity_extraction")
workflow.add_edge("entity_extraction", "summarization")
workflow.add_edge("summarization", END)

# 编译该图
app = workflow.compile()

sample_text = """
OpenAI发布了GPT‑4模型，这是一款大型多模态模型，在多项专业基准测试中展现出人类水平的性能。开发该模型旨在提升人工智能系统的对齐能力与安全性。
此外，相较于前代模型GPT‑3，该模型在设计上具备更高的运行效率与可扩展性。GPT‑4模型预计将在未来几个月内推出，可供公众用于研究与开发工作。
"""

state_input = {"text": sample_text}
result = app.invoke(state_input)

print("Classification:", result["classification"])
print("\nEntities:", result["entities"])
print("\nSummary:", result["summary"])
