from typing import List, Dict, Optional, Any
import logging
import os
import hashlib
import sqlite3
import time
import json
from dataclasses import dataclass, replace as dataclass_replace
from ..embedding import get_text_embedder, get_dimension
from ..storage.qdrant_store import QdrantVectorStore
from .acl_filter import ACLContext, filter_by_acl

logger = logging.getLogger(__name__)


def _get_markitdown_instance():
    """
    Get a configured MarkItDown instance for document conversion.
    """
    try:
        from markitdown import MarkItDown
        return MarkItDown()
    except ImportError:
        print("[WARNING] MarkItDown not available. Install with: pip install markitdown")
        return None


def _is_markitdown_supported_format(path: str) -> bool:
    """
    Check if the file format is supported by MarkItDown.
    Supports: PDF, Office docs (docx, xlsx, pptx), images (jpg, png, gif, bmp, tiff),
    audio (mp3, wav, m4a), HTML, text formats (txt, md, csv, json, xml), ZIP files, etc.
    """
    ext = (os.path.splitext(path)[1] or '').lower()
    supported_formats = {
        # Documents
        '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
        # Text formats
        '.txt', '.md', '.csv', '.json', '.xml', '.html', '.htm',
        # Images (OCR + metadata)
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp',
        # Audio (transcription + metadata)
        '.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg',
        # Archives
        '.zip', '.tar', '.gz', '.rar',
        # Code files
        '.py', '.js', '.ts', '.java', '.cpp', '.c', '.h', '.css', '.scss',
        # Other text
        '.log', '.conf', '.ini', '.cfg', '.yaml', '.yml', '.toml'
    }
    return ext in supported_formats


def _convert_to_markdown(path: str) -> str:
    """
    Universal document reader using MarkItDown with enhanced PDF processing.
    Converts any supported file format to markdown text.
    """
    if not os.path.exists(path):
        return ""

    # 对PDF文件使用增强处理
    ext = (os.path.splitext(path)[1] or '').lower()
    if ext == '.pdf':
        return _enhanced_pdf_processing(path)

    # 其他格式使用原有MarkItDown
    md_instance = _get_markitdown_instance()
    if md_instance is None:
        return _fallback_text_reader(path)

    try:
        result = md_instance.convert(path)
        text = getattr(result, "text_content", None)
        if isinstance(text, str) and text.strip():
            return text
        return ""
    except Exception as e:
        print(f"[WARNING] MarkItDown failed for {path}: {e}")
        return _fallback_text_reader(path)


def _enhanced_pdf_processing(path: str) -> str:
    """
    Enhanced PDF processing with post-processing cleanup.
    """
    print(f"[RAG] Using enhanced PDF processing for: {path}")

    # 使用原有MarkItDown提取
    md_instance = _get_markitdown_instance()
    if md_instance is None:
        return _fallback_text_reader(path)

    try:
        result = md_instance.convert(path)
        raw_text = getattr(result, "text_content", None)
        if not raw_text or not raw_text.strip():
            return ""

        # 后处理：清理和重组文本
        cleaned_text = _post_process_pdf_text(raw_text)
        print(f"[RAG] PDF post-processing completed: {len(raw_text)} -> {len(cleaned_text)} chars")
        return cleaned_text

    except Exception as e:
        print(f"[WARNING] Enhanced PDF processing failed for {path}: {e}")
        return _fallback_text_reader(path)


def _post_process_pdf_text(text: str) -> str:
    """
    Post-process PDF text to improve quality.
    """
    import re

    # 1. 按行分割并清理
    lines = text.splitlines()
    cleaned_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 移除单个字符的行（通常是噪音）
        if len(line) <= 2 and not line.isdigit():
            continue

        # 移除明显的页眉页脚噪音
        if re.match(r'^\d+$', line):  # 纯数字行（页码）
            continue
        if line.lower() in ['github', 'project', 'forks', 'stars', 'language']:
            continue

        cleaned_lines.append(line)

    # 2. 智能合并短行
    merged_lines = []
    i = 0

    while i < len(cleaned_lines):
        current_line = cleaned_lines[i]

        # 如果当前行很短，尝试与下一行合并
        if len(current_line) < 60 and i + 1 < len(cleaned_lines):
            next_line = cleaned_lines[i + 1]

            # 合并条件：都是内容，不是标题
            if (not current_line.endswith('：') and
                    not current_line.endswith(':') and
                    not current_line.startswith('#') and
                    not next_line.startswith('#') and
                    len(next_line) < 120):
                merged_line = current_line + " " + next_line
                merged_lines.append(merged_line)
                i += 2  # 跳过下一行
                continue

        merged_lines.append(current_line)
        i += 1

    # 3. 重新组织段落
    paragraphs = []
    current_paragraph = []

    for line in merged_lines:
        # 检查是否是新段落的开始
        if (line.startswith('#') or  # 标题
                line.endswith('：') or  # 中文冒号结尾
                line.endswith(':') or  # 英文冒号结尾
                len(line) > 150 or  # 长句通常是段落开始
                not current_paragraph):  # 第一行

            # 保存当前段落
            if current_paragraph:
                paragraphs.append(' '.join(current_paragraph))
                current_paragraph = []

            paragraphs.append(line)
        else:
            current_paragraph.append(line)

    # 添加最后一个段落
    if current_paragraph:
        paragraphs.append(' '.join(current_paragraph))

    return '\n\n'.join(paragraphs)


def _fallback_text_reader(path: str) -> str:
    """
    Simple fallback reader for basic text files when MarkItDown is unavailable.
    """
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except Exception:
        try:
            with open(path, 'r', encoding='latin-1', errors='ignore') as f:
                return f.read()
        except Exception:
            return ""


def _detect_lang(sample: str) -> str:
    try:
        from langdetect import detect
        return detect(sample[:1000]) if sample else "unknown"
    except Exception:
        return "unknown"


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
            0x4E00 <= code <= 0x9FFF or
            0x3400 <= code <= 0x4DBF or
            0x20000 <= code <= 0x2A6DF or
            0x2A700 <= code <= 0x2B73F or
            0x2B740 <= code <= 0x2B81F or
            0x2B820 <= code <= 0x2CEAF or
            0xF900 <= code <= 0xFAFF
    )


def _approx_token_len(text: str) -> int:
    # 近似估计：CJK字符按1 token，其他按空白分词
    cjk = sum(1 for ch in text if _is_cjk(ch))
    non_cjk_tokens = len([t for t in text.split() if t])
    return cjk + non_cjk_tokens


def _split_paragraphs_with_headings(text: str) -> List[Dict]:
    lines = text.splitlines()
    heading_stack: List[str] = []
    paragraphs: List[Dict] = []
    buf: List[str] = []
    char_pos = 0

    def flush_buf(end_pos: int):
        if not buf:
            return
        content = "\n".join(buf).strip()
        if not content:
            return
        paragraphs.append({
            "content": content,
            "heading_path": " > ".join(heading_stack) if heading_stack else None,
            "start": max(0, end_pos - len(content)),
            "end": end_pos,
        })

    for ln in lines:
        raw = ln
        if raw.strip().startswith("#"):
            # heading line
            flush_buf(char_pos)
            level = len(raw) - len(raw.lstrip('#'))
            title = raw.lstrip('#').strip()
            if level <= 0:
                level = 1
            if level <= len(heading_stack):
                heading_stack = heading_stack[:level - 1]
            heading_stack.append(title)
            char_pos += len(raw) + 1
            continue
        # paragraph accumulation
        if raw.strip() == "":
            flush_buf(char_pos)
            buf = []
        else:
            buf.append(raw)
        char_pos += len(raw) + 1
    flush_buf(char_pos)
    if not paragraphs:
        paragraphs = [{"content": text, "heading_path": None, "start": 0, "end": len(text)}]
    return paragraphs


def _chunk_paragraphs(paragraphs: List[Dict], chunk_tokens: int, overlap_tokens: int) -> List[Dict]:
    chunks: List[Dict] = []
    cur: List[Dict] = []
    cur_tokens = 0
    i = 0
    while i < len(paragraphs):
        p = paragraphs[i]
        p_tokens = _approx_token_len(p["content"]) or 1
        if cur_tokens + p_tokens <= chunk_tokens or not cur:
            cur.append(p)
            cur_tokens += p_tokens
            i += 1
        else:
            # emit current chunk
            content = "\n\n".join(x["content"] for x in cur)
            start = cur[0]["start"]
            end = cur[-1]["end"]
            heading_path = next((x["heading_path"] for x in reversed(cur) if x.get("heading_path")), None)
            chunks.append({
                "content": content,
                "start": start,
                "end": end,
                "heading_path": heading_path,
            })
            # build overlap by keeping tail tokens
            if overlap_tokens > 0 and cur:
                kept: List[Dict] = []
                kept_tokens = 0
                for x in reversed(cur):
                    t = _approx_token_len(x["content"]) or 1
                    if kept_tokens + t > overlap_tokens:
                        break
                    kept.append(x)
                    kept_tokens += t
                cur = list(reversed(kept))
                cur_tokens = kept_tokens
            else:
                cur = []
                cur_tokens = 0
    if cur:
        content = "\n\n".join(x["content"] for x in cur)
        start = cur[0]["start"]
        end = cur[-1]["end"]
        heading_path = next((x["heading_path"] for x in reversed(cur) if x.get("heading_path")), None)
        chunks.append({
            "content": content,
            "start": start,
            "end": end,
            "heading_path": heading_path,
        })
    return chunks


def load_and_chunk_texts(paths: List[str], chunk_size: int = 800, chunk_overlap: int = 100,
                         namespace: Optional[str] = None, source_label: str = "rag") -> List[Dict]:
    """
    Universal document loader and chunker using MarkItDown.
    Converts all supported formats to markdown, then chunks intelligently.
    """
    print(
        f"[RAG] Universal loader start: files={len(paths)} chunk_size={chunk_size} overlap={chunk_overlap} ns={namespace or 'default'}")
    chunks: List[Dict] = []
    seen_hashes = set()

    for path in paths:
        if not os.path.exists(path):
            print(f"[WARNING] File not found: {path}")
            continue

        print(f"[RAG] Processing: {path}")
        ext = (os.path.splitext(path)[1] or '').lower()

        # Convert to markdown using MarkItDown
        markdown_text = _convert_to_markdown(path)
        if not markdown_text.strip():
            print(f"[WARNING] No content extracted from: {path}")
            continue

        lang = _detect_lang(markdown_text)
        doc_id = hashlib.md5(f"{path}|{len(markdown_text)}".encode('utf-8')).hexdigest()

        # Always use markdown-aware chunking for better structure preservation
        para = _split_paragraphs_with_headings(markdown_text)
        token_chunks = _chunk_paragraphs(para, chunk_tokens=max(1, chunk_size), overlap_tokens=max(0, chunk_overlap))

        for ch in token_chunks:
            content = ch["content"]
            start = ch.get("start", 0)
            end = ch.get("end", start + len(content))
            norm = content.strip()
            if not norm:
                continue

            content_hash = hashlib.md5(norm.encode('utf-8')).hexdigest()
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)

            chunk_id = hashlib.md5(f"{doc_id}|{start}|{end}|{content_hash}".encode('utf-8')).hexdigest()
            chunks.append({
                "id": chunk_id,
                "content": content,
                "metadata": {
                    "source_path": path,
                    "file_ext": ext,
                    "doc_id": doc_id,
                    "lang": lang,
                    "start": start,
                    "end": end,
                    "content_hash": content_hash,
                    "namespace": namespace or "default",
                    "source": source_label,
                    "external": True,
                    "heading_path": ch.get("heading_path"),
                    "format": "markdown",  # Mark all content as markdown-processed
                },
            })

    print(f"[RAG] Universal loader done: total_chunks={len(chunks)}")
    return chunks


def build_graph_from_chunks(neo4j, chunks: List[Dict]) -> None:
    created_docs = set()
    for ch in chunks:
        mem_id = ch["id"]
        meta = ch.get("metadata", {})
        source_path = meta.get("source_path")
        doc_id = meta.get("doc_id")
        if doc_id and doc_id not in created_docs:
            created_docs.add(doc_id)
            try:
                neo4j.add_entity(
                    entity_id=doc_id,
                    name=os.path.basename(source_path or doc_id),
                    entity_type="Document",
                    properties={"source_path": source_path, "lang": meta.get("lang")}
                )
            except Exception:
                pass
        try:
            neo4j.add_entity(entity_id=mem_id, name=mem_id, entity_type="Memory", properties={
                "source_path": source_path,
                "doc_id": doc_id,
                "start": meta.get("start"),
                "end": meta.get("end"),
            })
        except Exception:
            pass
        if doc_id:
            try:
                neo4j.add_relationship(from_id=doc_id, to_id=mem_id, rel_type="HAS_CHUNK", properties={})
            except Exception:
                pass


def _preprocess_markdown_for_embedding(text: str) -> str:
    """
    Preprocess markdown text for better embedding quality.
    Removes excessive markup while preserving semantic content.
    """
    import re

    # Remove markdown headers symbols but keep the text
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)

    # Remove markdown links but keep the text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)

    # Remove markdown emphasis markers
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)  # bold
    text = re.sub(r'\*([^*]+)\*', r'\1', text)  # italic
    text = re.sub(r'`([^`]+)`', r'\1', text)  # inline code

    # Remove markdown code blocks but keep content
    text = re.sub(r'```[^\n]*\n([\s\S]*?)```', r'\1', text)

    # Remove excessive whitespace
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)

    return text.strip()


def _create_default_vector_store(dimension: int = None) -> QdrantVectorStore:
    """
    Create default Qdrant vector store with RAG-optimized settings.
    使用连接管理器避免重复连接。
    """
    if dimension is None:
        dimension = get_dimension(384)

    # Check for Qdrant configuration
    qdrant_url = os.getenv("QDRANT_URL")
    qdrant_api_key = os.getenv("QDRANT_API_KEY")

    # 使用连接管理器
    from ..storage.qdrant_store import QdrantConnectionManager
    return QdrantConnectionManager.get_instance(
        url=qdrant_url,
        api_key=qdrant_api_key,
        collection_name="hello_agents_rag_vectors",
        vector_size=dimension,
        distance="cosine"
    )


# Cache functions removed - using unified embedder with internal caching


def index_chunks(
        store=None,
        chunks: List[Dict] = None,
        cache_db: Optional[str] = None,
        batch_size: int = 64,
        rag_namespace: str = "default"
) -> None:
    """
    Index markdown chunks with unified embedding and Qdrant storage.
    Uses百炼 API with fallback to sentence-transformers.
    """
    if not chunks:
        print("[RAG] No chunks to index")
        return

    # Use unified embedding from embedding module
    embedder = get_text_embedder()
    dimension = get_dimension(384)

    # Create default Qdrant store if not provided
    if store is None:
        store = _create_default_vector_store(dimension)
        print(f"[RAG] Created default Qdrant store with dimension {dimension}")

    # Preprocess markdown texts for better embeddings
    processed_texts = []
    for c in chunks:
        raw_content = c["content"]
        processed_content = _preprocess_markdown_for_embedding(raw_content)
        processed_texts.append(processed_content)

    print(f"[RAG] Embedding start: total_texts={len(processed_texts)} batch_size={batch_size}")

    # Batch encoding with unified embedder
    vecs: List[List[float]] = []
    for i in range(0, len(processed_texts), batch_size):
        part = processed_texts[i:i + batch_size]
        try:
            # Use unified embedder directly (handles caching internally)
            part_vecs = embedder.encode(part)

            # Normalize to List[List[float]]
            if not isinstance(part_vecs, list):
                # 单个numpy数组转为列表中的列表
                if hasattr(part_vecs, "tolist"):
                    part_vecs = [part_vecs.tolist()]
                else:
                    part_vecs = [list(part_vecs)]
            else:
                # 检查是否是嵌套列表
                if part_vecs and not isinstance(part_vecs[0], (list, tuple)) and hasattr(part_vecs[0], "__len__"):
                    # numpy数组列表 -> 转换每个数组
                    normalized_vecs = []
                    for v in part_vecs:
                        if hasattr(v, "tolist"):
                            normalized_vecs.append(v.tolist())
                        else:
                            normalized_vecs.append(list(v))
                    part_vecs = normalized_vecs
                elif part_vecs and not isinstance(part_vecs[0], (list, tuple)):
                    # 单个向量被误判为列表，实际应该包装成[[...]]
                    if hasattr(part_vecs, "tolist"):
                        part_vecs = [part_vecs.tolist()]
                    else:
                        part_vecs = [list(part_vecs)]

            for v in part_vecs:
                try:
                    # 确保向量是float列表
                    if hasattr(v, "tolist"):
                        v = v.tolist()
                    v_norm = [float(x) for x in v]
                    if len(v_norm) != dimension:
                        print(f"[WARNING] 向量维度异常: 期望{dimension}, 实际{len(v_norm)}")
                        # 用零向量填充或截断
                        if len(v_norm) < dimension:
                            v_norm.extend([0.0] * (dimension - len(v_norm)))
                        else:
                            v_norm = v_norm[:dimension]
                    vecs.append(v_norm)
                except Exception as e:
                    print(f"[WARNING] 向量转换失败: {e}, 使用零向量")
                    vecs.append([0.0] * dimension)

        except Exception as e:
            print(f"[WARNING] Batch {i} encoding failed: {e}")
            print(f"[RAG] Retrying batch {i} with smaller chunks...")

            # 尝试重试：将批次分解为更小的块
            success = False
            for j in range(0, len(part), 8):  # 更小的批次
                small_part = part[j:j + 8]
                try:
                    import time
                    time.sleep(2)  # 等待2秒避免频率限制

                    small_vecs = embedder.encode(small_part)
                    # Normalize to List[List[float]]
                    if isinstance(small_vecs, list) and small_vecs and not isinstance(small_vecs[0], list):
                        small_vecs = [small_vecs]

                    for v in small_vecs:
                        if hasattr(v, "tolist"):
                            v = v.tolist()
                        try:
                            v_norm = [float(x) for x in v]
                            if len(v_norm) != dimension:
                                print(f"[WARNING] 向量维度异常: 期望{dimension}, 实际{len(v_norm)}")
                                if len(v_norm) < dimension:
                                    v_norm.extend([0.0] * (dimension - len(v_norm)))
                                else:
                                    v_norm = v_norm[:dimension]
                            vecs.append(v_norm)
                            success = True
                        except Exception as e2:
                            print(f"[WARNING] 小批次向量转换失败: {e2}")
                            vecs.append([0.0] * dimension)
                except Exception as e2:
                    print(f"[WARNING] 小批次 {j // 8} 仍然失败: {e2}")
                    # 为这个小批次创建零向量
                    for _ in range(len(small_part)):
                        vecs.append([0.0] * dimension)

            if not success:
                print(f"[ERROR] 批次 {i} 完全失败，使用零向量")

        print(f"[RAG] Embedding progress: {min(i + batch_size, len(processed_texts))}/{len(processed_texts)}")

    # Prepare metadata with RAG tags
    metas: List[Dict] = []
    ids: List[str] = []
    for ch in chunks:
        meta = {
            "memory_id": ch["id"],
            "user_id": "rag_user",
            "memory_type": "rag_chunk",
            "content": ch["content"],  # Keep original markdown content
            "data_source": "rag_pipeline",  # RAG identification tag
            "rag_namespace": rag_namespace,
            "is_rag_data": True,  # Clear RAG data marker
        }
        # Merge chunk metadata
        meta.update(ch.get("metadata", {}))
        metas.append(meta)
        ids.append(ch["id"])

    print(f"[RAG] Qdrant upsert start: n={len(vecs)}")
    success = store.add_vectors(vectors=vecs, metadata=metas, ids=ids)
    if success:
        print(f"[RAG] Qdrant upsert done: {len(vecs)} vectors indexed")
    else:
        print(f"[RAG] Qdrant upsert failed")
        raise RuntimeError("Failed to index vectors to Qdrant")


def embed_query(query: str) -> List[float]:
    """
    Embed query using unified embedding (百炼 with fallback).
    """
    embedder = get_text_embedder()
    dimension = get_dimension(384)
    try:
        vec = embedder.encode(query)

        # Normalize to List[float]
        if hasattr(vec, "tolist"):
            vec = vec.tolist()

        # 处理嵌套列表情况
        if isinstance(vec, list) and vec and isinstance(vec[0], (list, tuple)):
            vec = vec[0]  # Extract first vector if nested

        # 转换为float列表
        result = [float(x) for x in vec]

        # 检查维度
        if len(result) != dimension:
            print(f"[WARNING] Query向量维度异常: 期望{dimension}, 实际{len(result)}")
            # 用零向量填充或截断
            if len(result) < dimension:
                result.extend([0.0] * (dimension - len(result)))
            else:
                result = result[:dimension]

        return result
    except Exception as e:
        print(f"[WARNING] Query embedding failed: {e}")
        # Return zero vector as fallback
        return [0.0] * dimension


# ==================
# ACL 过滤（权限过滤内置在检索链路里）
# ==================
#
# 历史问题（修复记录）
# --------------------
# ``search_vectors`` 此前只支持 ``memory_type`` / ``is_rag_data`` /
# ``data_source`` / ``rag_namespace`` 四个过滤维度，**没有任何按用户 / 角色 /
# 归属过滤的入口**——任何用户的检索都可能召回别人的私有文档。判定项
# L1-5.4（P0，权限过滤）据此判为 BLOCKED。
#
# 修复方式不是「再提供一个需要调用方记得调用的过滤器」，而是把过滤**放进
# 检索函数本身**：越权条目在离开检索函数之前就被剔除，不可能进入模型上下文。
# ``acl_filter`` 提供判定逻辑（``filter_by_acl`` / ``is_visible``），本模块
# 只负责把它接到检索链路上。

#: 命中上出现这些字段即表示「不是公开文档」，需要身份才能判定可见性。
_ACL_MARKER_KEYS = ("acl_users", "acl_roles", "acl_tenants")


def _acl_document(hit: Dict) -> Dict:
    """把向量命中摊平成「文档」视图，供 ACL 判定使用。

    命中的正文与权限标注都挂在 ``metadata`` 上，而 ``acl_filter`` 判定的是
    文档级字段，因此这里摊平 metadata，并保留回原始命中的引用（过滤完成后
    要把**原始命中**还回去，而不是摊平后的视图）。
    """
    metadata = hit.get("metadata") or {}
    document = dict(metadata)
    document.setdefault("id", hit.get("id"))
    document["_hit"] = hit
    return document


def _has_acl_marker(document: Dict) -> bool:
    """判断文档是否带 ACL 标注（含显式拒绝）。"""
    if document.get("visible_to") is False:
        return True
    return any(document.get(key) for key in _ACL_MARKER_KEYS)


def _filter_hits_by_acl(
    hits: List[Dict],
    acl_context: Optional[ACLContext] = None,
    acl_default_deny: bool = False,
) -> List[Dict]:
    """按 ACL 过滤检索命中：越权条目不得进入模型上下文。

    过滤**永远发生**，不依赖调用方自觉：

    - 给了 ``acl_context``：按身份判定；``acl_default_deny`` 决定「没有 ACL
      标注的文档」是放行还是拒绝（安全敏感场景应传 True）。
    - 没给 ``acl_context``：**无法验证身份即无法授权**——带 ACL 标注（或显式
      ``visible_to=False``）的条目一律剔除。「有权限标注但没人来认领」正是
      越权读取最容易发生的地方，因此缺省不放行。

    :param hits: 向量库返回的原始命中
    :param acl_context: 调用者身份；``None`` 表示身份未知
    :param acl_default_deny: 无 ACL 标注的文档是否默认拒绝
    :return: 过滤后的原始命中列表
    """
    if not hits:
        return []
    documents = [_acl_document(hit) for hit in hits]
    if acl_context is None:
        kept = [
            hit for hit, document in zip(hits, documents)
            if not _has_acl_marker(document)
        ]
        dropped = len(hits) - len(kept)
        if dropped:
            logger.warning(
                "[RAG] ACL 过滤：未提供调用者身份，剔除 %d/%d 条带权限标注的命中",
                dropped,
                len(hits),
            )
        return kept
    kept_documents, report = filter_by_acl(
        documents, acl_context, default_deny=acl_default_deny
    )
    if report.had_violation:
        logger.warning("[RAG] %s", report.summary())
    return [document["_hit"] for document in kept_documents]


def search_vectors(
        store=None,
        query: str = "",
        top_k: int = 8,
        rag_namespace: Optional[str] = None,
        only_rag_data: bool = True,
        score_threshold: Optional[float] = None,
        acl_context: Optional[ACLContext] = None,
        acl_default_deny: bool = False,
) -> List[Dict]:
    """
    Search RAG vectors using unified embedding and Qdrant.

    检索结果**必定**经过 ACL 过滤才返回（见 :func:`_filter_hits_by_acl`）：
    给了 ``acl_context`` 就按身份判定，没给就把带权限标注的条目剔除。
    权限过滤因此不依赖调用方自觉调用，越权条目也不可能进入模型上下文。
    """
    if not query or not query.strip():
        return []

    # Create default store if not provided
    if store is None:
        try:
            store = _create_default_vector_store()
        except Exception as e:
            # 向量库不可用时**安全降级**：检索是可选能力，不该把异常抛给调用方
            # （此前这里直接抛 Qdrant 连接错误，连空白查询都会命中该路径）。
            print(f"[WARNING] RAG vector store unavailable, degrade to empty result: {e}")
            return []

    # Embed query with unified embedder
    qv = embed_query(query)

    # Build filter for RAG data
    where = {"memory_type": "rag_chunk"}
    if only_rag_data:
        where["is_rag_data"] = True
        where["data_source"] = "rag_pipeline"
    if rag_namespace:
        where["rag_namespace"] = rag_namespace

    try:
        hits = store.search_similar(
            query_vector=qv,
            limit=top_k,
            score_threshold=score_threshold,
            where=where
        )
    except Exception as e:
        print(f"[WARNING] RAG search failed: {e}")
        return []
    # ACL 过滤必须在结果**离开检索函数之前**完成：条目一旦返回，就可能
    # 被拼进模型上下文。
    return _filter_hits_by_acl(hits, acl_context, acl_default_deny)


def _prompt_mqe(query: str, n: int) -> List[str]:
    try:
        from ...core.llm import HelloAgentsLLM
        llm = HelloAgentsLLM()
        prompt = [
            {"role": "system",
             "content": "你是检索查询扩展助手。生成语义等价或互补的多样化查询。使用中文，简短，避免标点。"},
            {"role": "user", "content": f"原始查询：{query}\n请给出{n}个不同表述的查询，每行一个。"}
        ]
        text = llm.invoke(prompt)
        lines = [ln.strip("- \t") for ln in (text or "").splitlines()]
        outs = [ln for ln in lines if ln]
        return outs[:n] or [query]
    except Exception:
        return [query]


def _prompt_hyde(query: str) -> Optional[str]:
    try:
        from ...core.llm import HelloAgentsLLM
        llm = HelloAgentsLLM()
        prompt = [
            {"role": "system",
             "content": "根据用户问题，先写一段可能的答案性段落，用于向量检索的查询文档（不要分析过程）。"},
            {"role": "user", "content": f"问题：{query}\n请直接写一段中等长度、客观、包含关键术语的段落。"}
        ]
        return llm.invoke(prompt)
    except Exception:
        return None


def search_vectors_expanded(
        store=None,
        query: str = "",
        top_k: int = 8,
        rag_namespace: Optional[str] = None,
        only_rag_data: bool = True,
        score_threshold: Optional[float] = None,
        enable_mqe: bool = False,
        mqe_expansions: int = 2,
        enable_hyde: bool = False,
        candidate_pool_multiplier: int = 4,
        acl_context: Optional[ACLContext] = None,
        acl_default_deny: bool = False,
) -> List[Dict]:
    """
    Search with query expansion using unified embedding and Qdrant.

    与 :func:`search_vectors` 一样，**返回前必定经过 ACL 过滤**：越权条目
    在合并排序之前就被剔除，不会占用 TopK 名额。
    """
    if not query or not query.strip():
        return []

    # Create default store if not provided
    if store is None:
        try:
            store = _create_default_vector_store()
        except Exception as e:
            # 向量库不可用时**安全降级**：检索是可选能力，不该把异常抛给调用方
            # （此前这里直接抛 Qdrant 连接错误，连空白查询都会命中该路径）。
            print(f"[WARNING] RAG vector store unavailable, degrade to empty result: {e}")
            return []

    # expansions
    expansions: List[str] = [query]

    if enable_mqe and mqe_expansions > 0:
        expansions.extend(_prompt_mqe(query, mqe_expansions))
    if enable_hyde:
        hyde_text = _prompt_hyde(query)
        if hyde_text:
            expansions.append(hyde_text)

    # unique and trim
    uniq: List[str] = []
    for e in expansions:
        if e and e not in uniq:
            uniq.append(e)
    expansions = uniq[: max(1, len(uniq))]

    # distribute pool per expansion
    pool = max(top_k * candidate_pool_multiplier, 20)
    per = max(1, pool // max(1, len(expansions)))

    # Build filter for RAG data
    where = {"memory_type": "rag_chunk"}
    if only_rag_data:
        where["is_rag_data"] = True
        where["data_source"] = "rag_pipeline"
    if rag_namespace:
        where["rag_namespace"] = rag_namespace

    # collect hits across expansions
    agg: Dict[str, Dict] = {}
    for q in expansions:
        qv = embed_query(q)
        try:
            hits = store.search_similar(query_vector=qv, limit=per, score_threshold=score_threshold, where=where)
        except Exception as e:
            # 向量库中途不可用时安全降级为「这一路扩展无命中」，
            # 而不是把异常抛给调用方。
            print(f"[WARNING] RAG search failed for expansion: {e}")
            continue
        for h in hits:
            mid = h.get("metadata", {}).get("memory_id", h.get("id"))
            s = float(h.get("score", 0.0))
            if mid not in agg or s > float(agg[mid].get("score", 0.0)):
                agg[mid] = h
    # return top by score
    merged = list(agg.values())
    # ACL 过滤先于排序与截断：越权条目既不进入结果，也不占 TopK 名额。
    merged = _filter_hits_by_acl(merged, acl_context, acl_default_deny)
    merged.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
    return merged[:top_k]


def _try_load_cross_encoder(model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
    try:
        from sentence_transformers import CrossEncoder
        return CrossEncoder(model_name)
    except Exception:
        return None


def rerank_with_cross_encoder(query: str, items: List[Dict], model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
                              top_k: int = 10) -> List[Dict]:
    ce = _try_load_cross_encoder(model_name)
    if ce is None or not items:
        return items[:top_k]
    pairs = [[query, it.get("content", "")] for it in items]
    try:
        scores = ce.predict(pairs)
        for it, s in zip(items, scores):
            it["rerank_score"] = float(s)
        items.sort(key=lambda x: x.get("rerank_score", x.get("score", 0.0)), reverse=True)
        return items[:top_k]
    except Exception:
        return items[:top_k]


def compute_graph_signals_from_pool(vector_hits: List[Dict], same_doc_weight: float = 1.0,
                                    proximity_weight: float = 1.0, proximity_window_chars: int = 1600) -> Dict[
    str, float]:
    """
    Compute graph signals with direct parameters instead of environment variables.
    """

    # group by doc
    by_doc: Dict[str, List[Dict]] = {}
    for h in vector_hits:
        meta = h.get("metadata", {})
        did = meta.get("doc_id")
        if not did:
            # fall back to memory_id grouping if doc missing
            did = meta.get("memory_id") or h.get("id")
        by_doc.setdefault(did, []).append(h)

    # same-doc density score
    doc_counts = {d: len(arr) for d, arr in by_doc.items()}
    max_count = max(doc_counts.values()) if doc_counts else 1

    # proximity score per hit within same doc
    graph_signal: Dict[str, float] = {}
    for did, arr in by_doc.items():
        arr.sort(key=lambda x: x.get("metadata", {}).get("start", 0))
        # precompute density
        density = doc_counts.get(did, 1) / max_count
        # proximity accumulation
        for i, h in enumerate(arr):
            mid = h.get("metadata", {}).get("memory_id", h.get("id"))
            pos_i = h.get("metadata", {}).get("start", 0)
            prox_acc = 0.0
            # look around neighbors within window
            # two-pointer expansion
            # left
            j = i - 1
            while j >= 0:
                pos_j = arr[j].get("metadata", {}).get("start", 0)
                dist = abs(pos_i - pos_j)
                if dist > proximity_window_chars:
                    break
                prox_acc += max(0.0, 1.0 - (dist / max(1.0, float(proximity_window_chars))))
                j -= 1
            # right
            j = i + 1
            while j < len(arr):
                pos_j = arr[j].get("metadata", {}).get("start", 0)
                dist = abs(pos_i - pos_j)
                if dist > proximity_window_chars:
                    break
                prox_acc += max(0.0, 1.0 - (dist / max(1.0, float(proximity_window_chars))))
                j += 1
            # combine
            score = same_doc_weight * density + proximity_weight * prox_acc
            graph_signal[mid] = graph_signal.get(mid, 0.0) + score

    # normalize to [0,1]
    if graph_signal:
        max_v = max(graph_signal.values())
        if max_v > 0:
            for k in list(graph_signal.keys()):
                graph_signal[k] = graph_signal[k] / max_v
    return graph_signal


def rank(vector_hits: List[Dict], graph_signals: Optional[Dict[str, float]] = None, w_vector: float = 0.7,
         w_graph: float = 0.3) -> List[Dict]:
    """
    Rank results with direct weight parameters instead of environment variables.
    """
    items: List[Dict] = []
    graph_signals = graph_signals or {}
    for h in vector_hits:
        mid = h.get("metadata", {}).get("memory_id", h.get("id"))
        g = float(graph_signals.get(mid, 0.0))
        v = float(h.get("score", 0.0))
        score = w_vector * v + w_graph * g
        items.append({
            "memory_id": mid,
            "score": score,
            "vector_score": v,
            "graph_score": g,
            "content": h.get("metadata", {}).get("content", ""),
            "metadata": h.get("metadata", {}),
        })
    items.sort(key=lambda x: x["score"], reverse=True)
    return items


def merge_snippets(ranked_items: List[Dict], max_chars: int = 1200) -> str:
    out: List[str] = []
    total = 0
    for it in ranked_items:
        text = it.get("content", "").strip()
        if not text:
            continue
        if total + len(text) > max_chars:
            remain = max_chars - total
            if remain <= 0:
                break
            out.append(text[:remain])
            total += remain
            break
        out.append(text)
        total += len(text)
    return "\n\n".join(out)


def expand_neighbors_from_pool(selected: List[Dict], pool: List[Dict], neighbors: int = 1, max_additions: int = 5) -> \
List[Dict]:
    if not selected or not pool or neighbors <= 0:
        return selected
    # index pool by doc_id and sort by start
    by_doc: Dict[str, List[Dict]] = {}
    for it in pool:
        meta = it.get("metadata", {})
        did = meta.get("doc_id")
        if not did:
            continue
        by_doc.setdefault(did, []).append(it)
    for did, arr in by_doc.items():
        arr.sort(key=lambda x: (x.get("metadata", {}).get("start", 0)))
    selected_ids = set(it.get("memory_id") for it in selected)
    additions: List[Dict] = []
    for it in selected:
        meta = it.get("metadata", {})
        did = meta.get("doc_id")
        if not did or did not in by_doc:
            continue
        arr = by_doc[did]
        # find index
        try:
            idx = next(i for i, x in enumerate(arr) if x.get("memory_id") == it.get("memory_id"))
        except StopIteration:
            continue
        for offset in range(1, neighbors + 1):
            for j in (idx - offset, idx + offset):
                if 0 <= j < len(arr):
                    cand = arr[j]
                    mid = cand.get("memory_id")
                    if mid not in selected_ids:
                        additions.append(cand)
                        selected_ids.add(mid)
                        if len(additions) >= max_additions:
                            break
            if len(additions) >= max_additions:
                break
        if len(additions) >= max_additions:
            break
    # keep relative order by score
    extended = list(selected) + additions
    extended.sort(key=lambda x: (x.get("rerank_score", x.get("score", 0.0))), reverse=True)
    return extended


def merge_snippets_grouped(ranked_items: List[Dict], max_chars: int = 1200, include_citations: bool = True) -> str:
    # Group by doc_id and aggregate doc score
    by_doc: Dict[str, List[Dict]] = {}
    doc_score: Dict[str, float] = {}
    for it in ranked_items:
        meta = it.get("metadata", {})
        did = meta.get("doc_id") or meta.get("source_path") or "unknown"
        by_doc.setdefault(did, []).append(it)
        doc_score[did] = doc_score.get(did, 0.0) + float(it.get("score", 0.0))
    # Sort docs by aggregate score
    ordered_docs = sorted(by_doc.keys(), key=lambda d: doc_score.get(d, 0.0), reverse=True)
    # Within doc, order by start offset to preserve context
    for d in ordered_docs:
        by_doc[d].sort(key=lambda x: (x.get("metadata", {}).get("start", 0)))
    out: List[str] = []
    citations: List[Dict] = []
    total = 0
    cite_index = 1
    for did in ordered_docs:
        parts = by_doc[did]
        for it in parts:
            text = (it.get("content", "") or "").strip()
            if not text:
                continue
            # add citation marker if enabled
            suffix = ""
            if include_citations:
                suffix = f" [{cite_index}]"
            need = len(text) + (len(suffix) if suffix else 0)
            if total + need > max_chars:
                remain = max_chars - total
                if remain <= 0:
                    break
                clipped = text[: max(0, remain - len(suffix))]
                if clipped:
                    out.append(clipped + suffix)
                    total += len(clipped) + len(suffix)
                    if include_citations:
                        m = it.get("metadata", {})
                        citations.append({
                            "index": cite_index,
                            "source_path": m.get("source_path"),
                            "doc_id": m.get("doc_id"),
                            "start": m.get("start"),
                            "end": m.get("end"),
                            "heading_path": m.get("heading_path"),
                        })
                        cite_index += 1
                break
            out.append(text + suffix)
            total += need
            if include_citations:
                m = it.get("metadata", {})
                citations.append({
                    "index": cite_index,
                    "source_path": m.get("source_path"),
                    "doc_id": m.get("doc_id"),
                    "start": m.get("start"),
                    "end": m.get("end"),
                    "heading_path": m.get("heading_path"),
                })
                cite_index += 1
        if total >= max_chars:
            break
    merged = "\n\n".join(out)
    if include_citations and citations:
        lines: List[str] = [merged, "", "References:"]
        for c in citations:
            loc = ""
            if c.get("start") is not None and c.get("end") is not None:
                loc = f" ({c['start']}-{c['end']})"
            hp = f" – {c['heading_path']}" if c.get("heading_path") else ""
            sp = c.get("source_path") or c.get("doc_id") or "source"
            lines.append(f"[{c['index']}] {sp}{loc}{hp}")
        return "\n".join(lines)
    return merged


def compress_ranked_items(ranked_items: List[Dict], enable_compression: bool = True, max_per_doc: int = 2,
                          join_gap: int = 200) -> List[Dict]:
    """
    Compress ranked items with direct parameters instead of environment variables.
    """
    if not enable_compression:
        return ranked_items
    by_doc_count: Dict[str, int] = {}
    last_by_doc: Dict[str, Dict] = {}
    new_items: List[Dict] = []
    for it in ranked_items:
        meta = it.get("metadata", {})
        did = meta.get("doc_id") or meta.get("source_path") or "unknown"
        start = int(meta.get("start") or 0)
        end = int(meta.get("end") or (start + len(it.get("content", "") or "")))
        if did not in last_by_doc:
            last_by_doc[did] = it
            by_doc_count[did] = 1
            new_items.append(it)
            continue
        last = last_by_doc[did]
        lmeta = last.get("metadata", {})
        lstart = int(lmeta.get("start") or 0)
        lend = int(lmeta.get("end") or (lstart + len(last.get("content", "") or "")))
        if start - lend <= join_gap and start >= lstart:
            # merge into last
            merged_text = (last.get("content", "") or "").strip()
            add_text = (it.get("content", "") or "").strip()
            if add_text:
                if merged_text:
                    merged_text = merged_text + "\n\n" + add_text
                else:
                    merged_text = add_text
                last["content"] = merged_text
                lmeta["end"] = max(lend, end)
                # keep the higher score
                try:
                    last["score"] = max(float(last.get("score", 0.0)), float(it.get("score", 0.0)))
                except Exception:
                    pass
            last_by_doc[did] = last
        else:
            cnt = by_doc_count.get(did, 0)
            if cnt >= max_per_doc:
                continue
            new_items.append(it)
            last_by_doc[did] = it
            by_doc_count[did] = cnt + 1
    return new_items


def tldr_summarize(text: str, bullets: int = 3) -> Optional[str]:
    try:
        if not text or len(text.strip()) == 0:
            return None
        from ...core.llm import HelloAgentsLLM
        llm = HelloAgentsLLM()
        prompt = [
            {"role": "system", "content": "请将以下内容概括为简洁的要点列表（最多3-5条），用中文，避免重复，突出关键信息。"},
            {"role": "user", "content": f"请用 {max(1, min(5, int(bullets)))} 条要点总结：\n\n{text}"},
        ]
        out = llm.invoke(prompt)
        return out
    except Exception:
        return None


# ==================
# Three-stage retrieval (recall -> rerank -> compress)
# ==================
#
# 历史问题：``rerank_with_cross_encoder`` / ``compress_ranked_items`` /
# ``expand_neighbors_from_pool`` / ``tldr_summarize`` / ``merge_snippets_grouped``
# 五个函数此前**只有定义没有调用方**，主链路止步于 ``search_vectors_expanded``
# （查询扩展 + 向量召回 + 去重取 TopK），所谓「三段式压缩检索」实际只跑了一段。
#
# 本模块把三段真正接起来，**降级是显式且可观测的**：每一段都先看依赖是否可用
# （交叉编码器模型、邻居候选池、LLM 摘要），不可用就在 ``report`` 里写明走了
# 哪条兜底路径，而不是静默退化成「什么都没做」。


@dataclass(frozen=True)
class ThreeStageRetrievalConfig:
    """三段式检索的运行参数。

    - **段一 Recall**：查询扩展（MQE / HyDE）+ 向量召回 -> 候选池；
    - **段二 Rerank**：交叉编码器重排；模型不可用时回落到图信号融合
      （``compute_graph_signals_from_pool`` + ``rank``），再做邻居扩展；
    - **段三 Compress**：同文档片段合并 + 预算裁剪 +（可选）TLDR 摘要，
      产出带引用的上下文串。
    """

    # --- 段一 ---
    enable_query_expansion: bool = True
    enable_mqe: bool = True
    mqe_expansions: int = 2
    enable_hyde: bool = True
    candidate_pool_multiplier: int = 4

    # --- 段二 ---
    enable_rerank: bool = True
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rerank_fallback_to_graph: bool = True
    w_vector: float = 0.7
    w_graph: float = 0.3
    enable_neighbor_expand: bool = True
    neighbors: int = 1
    max_additions: int = 5

    # --- 段三 ---
    enable_compression: bool = True
    max_per_doc: int = 2
    join_gap: int = 200
    enable_tldr: bool = False
    tldr_bullets: int = 3
    max_chars: int = 1200
    include_citations: bool = True

    # --- 通用 ---
    top_k: int = 8


def _normalize_hit(hit: Dict) -> Dict:
    """把向量库原始命中统一成三段式内部的条目形状。

    原始命中只有 ``{"id", "score", "metadata"}``，正文在 ``metadata["content"]``
    里；而后段的函数（重排 / 邻居扩展 / 片段合并）都按**顶层** ``content`` 取正文。
    少了这一步，交叉编码器拿到的是空串，重排分数全部失真。
    """
    meta = hit.get("metadata") or {}
    return {
        "memory_id": meta.get("memory_id") or hit.get("id"),
        "score": float(hit.get("score", 0.0) or 0.0),
        "content": meta.get("content", "") or "",
        "metadata": dict(meta),
    }


def _sync_content_to_metadata(items: List[Dict]) -> None:
    """把合并后的正文写回 ``metadata["content"]``。

    调用方（``rag_tool`` 等）历史上从 ``metadata["content"]`` 取正文；段三会把
    相邻片段拼成一个条目，不同步的话老调用方拿到的仍是**未合并的旧文本**。
    """
    for it in items:
        meta = it.get("metadata")
        if isinstance(meta, dict):
            meta["content"] = it.get("content", "") or ""


def search_three_stage(
        store=None,
        query: str = "",
        top_k: int = 8,
        rag_namespace: Optional[str] = None,
        only_rag_data: bool = True,
        score_threshold: Optional[float] = None,
        config: Optional[ThreeStageRetrievalConfig] = None,
        acl_context: Optional[ACLContext] = None,
        acl_default_deny: bool = False,
        **overrides: Any,
) -> Dict[str, Any]:
    """执行完整的三段式检索：召回 -> 重排 -> 压缩。

    :param config: 三段式参数；缺省用默认配置。
    :param overrides: 覆盖 ``config`` 的同名字段（便于按调用点临时关掉某一段）。
    :param acl_context: 调用者身份；``None`` 表示身份未知。
    :param acl_default_deny: 无 ACL 标注的文档是否默认拒绝。
    :return: ``{"hits", "context", "citations", "report"}``。
             ``hits`` 是段三产出的最终条目（兼容旧的列表消费方式），
             ``context`` 是带引用的上下文串，``report`` 记录每段走了哪条路径。
    """
    cfg = config or ThreeStageRetrievalConfig()
    if overrides:
        known = {k: v for k, v in overrides.items() if hasattr(cfg, k)}
        if known:
            cfg = dataclass_replace(cfg, **known)

    if not query or not query.strip():
        return {"hits": [], "context": "", "citations": [], "report": {"empty_query": True}}

    # ------------------------------------------------------------------
    # Stage 1 — recall: query expansion + vector search -> candidate pool
    # ------------------------------------------------------------------
    # 段二要能在「比 top_k 更大的池子」里重新排序，所以这里故意把召回上限放大；
    # ``candidate_pool_multiplier`` 传 1，避免池子被二次放大。
    recall_limit = max(int(top_k) * max(1, int(cfg.candidate_pool_multiplier)), 20)

    if cfg.enable_query_expansion:
        pool_hits = search_vectors_expanded(
            store=store,
            query=query,
            top_k=recall_limit,
            rag_namespace=rag_namespace,
            only_rag_data=only_rag_data,
            score_threshold=score_threshold,
            enable_mqe=cfg.enable_mqe,
            mqe_expansions=cfg.mqe_expansions,
            enable_hyde=cfg.enable_hyde,
            candidate_pool_multiplier=1,
            acl_context=acl_context,
            acl_default_deny=acl_default_deny,
        )
    else:
        pool_hits = search_vectors(
            store=store,
            query=query,
            top_k=recall_limit,
            rag_namespace=rag_namespace,
            only_rag_data=only_rag_data,
            score_threshold=score_threshold,
            acl_context=acl_context,
            acl_default_deny=acl_default_deny,
        )

    pool = [_normalize_hit(h) for h in pool_hits]
    if not pool:
        return {
            "hits": [],
            "context": "",
            "citations": [],
            "report": {
                "query": query,
                "recall_limit": recall_limit,
                "pool_size": 0,
                "rerank_strategy": "skipped_empty_pool",
                "expanded_additions": 0,
                "final_items": 0,
                "context_chars": 0,
            },
        }

    # ------------------------------------------------------------------
    # Stage 2 — rerank: cross encoder, falling back to graph-signal fusion
    # ------------------------------------------------------------------
    rerank_strategy = "none"
    # 取 TopK 前的排序基准：段一给的是纯向量分。
    ranked: List[Dict] = list(pool)

    if cfg.enable_rerank:
        reranked = rerank_with_cross_encoder(
            query, ranked, model_name=cfg.rerank_model, top_k=len(ranked)
        )
        if any("rerank_score" in item for item in reranked):
            ranked = reranked
            rerank_strategy = "cross_encoder"
        elif cfg.rerank_fallback_to_graph:
            # 交叉编码器不可用（未装 sentence-transformers / 无法下载权重）时的兜底：
            # 同一文档的命中密度 + 位置邻近度作为图信号，与向量分线性融合。
            signals = compute_graph_signals_from_pool(pool_hits)
            ranked = rank(pool_hits, signals, cfg.w_vector, cfg.w_graph)
            rerank_strategy = "graph_fusion"
        else:
            ranked = reranked
            rerank_strategy = "passthrough"

    selected = ranked[:max(1, int(top_k))]

    expanded_additions = 0
    if cfg.enable_neighbor_expand and selected:
        extended = expand_neighbors_from_pool(
            selected,
            pool,
            neighbors=cfg.neighbors,
            max_additions=cfg.max_additions,
        )
        expanded_additions = len(extended) - len(selected)
        selected = extended

    # ------------------------------------------------------------------
    # Stage 3 — compress: same-doc merge + budget trim (+ optional TLDR)
    # ------------------------------------------------------------------
    if cfg.enable_compression:
        selected = compress_ranked_items(
            selected,
            enable_compression=True,
            max_per_doc=cfg.max_per_doc,
            join_gap=cfg.join_gap,
        )

    # 上下文拼装**之前**再过一道 ACL：候选池已经过滤过，这里是纵深防御
    # （邻居扩展与同文档合并都不该把越权内容带进上下文）。
    final_items = _filter_hits_by_acl(
        selected[:max(1, int(top_k))], acl_context, acl_default_deny
    )
    _sync_content_to_metadata(final_items)

    context = merge_snippets_grouped(
        final_items,
        max_chars=cfg.max_chars,
        include_citations=cfg.include_citations,
    )

    if cfg.enable_tldr:
        summary = tldr_summarize(context, bullets=cfg.tldr_bullets)
        if summary:
            context = f"{context}\n\nTL;DR:\n{summary}"

    # 引用列表：只对真正进入上下文的条目给引用（超过字符预算被截断的不算）。
    citations: List[Dict] = []
    for index, item in enumerate(final_items, start=1):
        snippet = (item.get("content", "") or "").strip()[:60]
        if not snippet or snippet not in context:
            continue
        meta = item.get("metadata", {})
        citations.append({
            "index": index,
            "source_path": meta.get("source_path"),
            "doc_id": meta.get("doc_id"),
            "start": meta.get("start"),
            "end": meta.get("end"),
            "heading_path": meta.get("heading_path"),
            "score": item.get("score", 0.0),
            "rerank_score": item.get("rerank_score"),
        })

    report = {
        "query": query,
        "recall_limit": recall_limit,
        "pool_size": len(pool),
        "rerank_strategy": rerank_strategy,
        "expanded_additions": expanded_additions,
        "final_items": len(final_items),
        "context_chars": len(context),
        "acl_enforced": True,
        "acl_context_provided": acl_context is not None,
        "stages": {
            "query_expansion": bool(cfg.enable_query_expansion),
            "rerank": bool(cfg.enable_rerank),
            "neighbor_expand": bool(cfg.enable_neighbor_expand),
            "compression": bool(cfg.enable_compression),
            "tldr": bool(cfg.enable_tldr),
        },
    }

    return {
        "hits": final_items,
        "context": context,
        "citations": citations,
        "report": report,
    }


# ==================
# High-level RAG Pipeline API
# ==================

def create_rag_pipeline(
        qdrant_url: Optional[str] = None,
        qdrant_api_key: Optional[str] = None,
        collection_name: str = "hello_agents_rag_vectors",
        rag_namespace: str = "default"
) -> Dict[str, Any]:
    """
    Create a complete RAG pipeline with Qdrant and unified embedding.

    Returns:
        Dict containing store, namespace, and helper functions
    """
    dimension = get_dimension(384)

    store = QdrantVectorStore(
        url=qdrant_url,
        api_key=qdrant_api_key,
        collection_name=collection_name,
        vector_size=dimension,
        distance="cosine"
    )

    def add_documents(file_paths: List[str], chunk_size: int = 800, chunk_overlap: int = 100):
        """Add documents to RAG pipeline"""
        chunks = load_and_chunk_texts(
            paths=file_paths,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            namespace=rag_namespace,
            source_label="rag"
        )
        index_chunks(
            store=store,
            chunks=chunks,
            rag_namespace=rag_namespace
        )
        return len(chunks)

    def search(query: str, top_k: int = 8, score_threshold: Optional[float] = None,
               acl_context: Optional[ACLContext] = None,
               acl_default_deny: bool = False):
        """Search RAG knowledge base (stage 1 only: plain vector recall)."""
        return search_vectors(
            store=store,
            query=query,
            top_k=top_k,
            rag_namespace=rag_namespace,
            score_threshold=score_threshold,
            acl_context=acl_context,
            acl_default_deny=acl_default_deny,
        )

    def search_advanced(
            query: str,
            top_k: int = 8,
            enable_mqe: bool = False,
            enable_hyde: bool = False,
            score_threshold: Optional[float] = None,
            acl_context: Optional[ACLContext] = None,
            acl_default_deny: bool = False
    ):
        """Advanced search with query expansion.

        现在真正走三段式（召回 -> 重排 -> 压缩）：此前这里只做查询扩展 +
        向量召回，「重排 / 压缩」五个函数全项目零调用方。签名保持旧契约，
        返回的是段三产出的**条目列表**（与旧返回值形状一致）。
        """
        result = search_three_stage(
            store=store,
            query=query,
            top_k=top_k,
            rag_namespace=rag_namespace,
            score_threshold=score_threshold,
            config=ThreeStageRetrievalConfig(
                enable_mqe=enable_mqe,
                enable_hyde=enable_hyde,
                top_k=top_k,
            ),
            acl_context=acl_context,
            acl_default_deny=acl_default_deny,
        )
        return result["hits"]

    def search_full(query: str, top_k: int = 8, score_threshold: Optional[float] = None,
                    config: Optional[ThreeStageRetrievalConfig] = None,
                    acl_context: Optional[ACLContext] = None,
                    acl_default_deny: bool = False, **overrides: Any):
        """Three-stage retrieval with the stage report attached.

        :return: ``{"hits", "context", "citations", "report"}``。
        """
        return search_three_stage(
            store=store,
            query=query,
            top_k=top_k,
            rag_namespace=rag_namespace,
            score_threshold=score_threshold,
            config=config,
            acl_context=acl_context,
            acl_default_deny=acl_default_deny,
            **overrides,
        )

    def get_stats():
        """Get pipeline statistics"""
        return store.get_collection_stats()

    return {
        "store": store,
        "namespace": rag_namespace,
        "add_documents": add_documents,
        "search": search,
        "search_advanced": search_advanced,
        "search_full": search_full,
        "get_stats": get_stats
    }