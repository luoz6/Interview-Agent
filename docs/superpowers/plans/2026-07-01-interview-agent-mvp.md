# 面试 Agent MVP 实施计划

> **给后续执行者：** 这个计划按 SDD + TDD 编写。实现时每个模块先写测试，再写最小实现，再运行测试确认通过。

**目标：** 构建一个最小可运行的 FastAPI + 静态页面面试 Agent MVP，支持 JD/简历预热和文本模拟面试。

**架构：** 业务逻辑放在小型 service 模块中，API 层只做请求和响应转换，前端使用无构建步骤的静态 HTML/CSS/JS。第一版使用确定性规则保证测试稳定，后续再用 LangChain 和 LangGraph 替换内部实现。

**技术栈：** Python、FastAPI、pytest、静态 HTML/CSS/JavaScript。

---

### 任务 1：预热服务

**文件：**
- 创建：`app/services/prep.py`
- 测试：`tests/test_prep_service.py`

- [x] **步骤 1：先写失败测试**

- [x] **步骤 2：运行测试确认失败**

命令：`python -m pytest tests/test_prep_service.py -v`

- [x] **步骤 3：写最小实现**

- [x] **步骤 4：运行测试确认通过**

命令：`python -m pytest tests/test_prep_service.py -v`

### 任务 2：会话服务

**文件：**
- 创建：`app/services/session.py`
- 测试：`tests/test_session_service.py`

- [x] **步骤 1：先写失败测试**

- [x] **步骤 2：运行测试确认失败**

命令：`python -m pytest tests/test_session_service.py -v`

- [x] **步骤 3：写最小实现**

- [x] **步骤 4：运行测试确认通过**

命令：`python -m pytest tests/test_session_service.py -v`

### 任务 3：FastAPI 接口

**文件：**
- 创建：`app/api/routes.py`
- 修改：`app/main.py`
- 测试：`tests/test_api.py`

- [x] **步骤 1：先写 API 失败测试**

- [x] **步骤 2：运行测试确认失败**

命令：`python -m pytest tests/test_api.py -v`

- [x] **步骤 3：实现接口路由**

- [x] **步骤 4：运行测试确认通过**

命令：`python -m pytest tests/test_api.py -v`

### 任务 4：静态页面

**文件：**
- 创建：`app/static/index.html`
- 创建：`app/static/styles.css`
- 创建：`app/static/app.js`

- [x] **步骤 1：构建无前端构建步骤的浏览器界面**

- [x] **步骤 2：确认应用能提供页面**

命令：`python -m uvicorn app.main:app --reload`

### 任务 5：项目说明和依赖

**文件：**
- 创建：`requirements.txt`
- 创建：`README.md`
- 创建：`app/__init__.py`
- 创建：`app/api/__init__.py`
- 创建：`app/services/__init__.py`

- [x] **步骤 1：补充依赖和运行说明**

- [x] **步骤 2：运行全部测试**

命令：`python -m pytest -v`
