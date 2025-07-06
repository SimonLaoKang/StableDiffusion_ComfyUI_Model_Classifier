# StableDiffusion / ComfyUI模型分类工具

## 简介

本工具用于 Stable Diffusion / ComfyUI 模型的批量管理、分类、查重、重命名、移动、备注编辑、预览图片管理等，支持多种模型格式（如 `.ckpt`, `.safetensors`, `.pth`, `.pt`, `.bin`, `.th`, `.gguf`），并可导出 Excel/JSON 报表。

## 主要功能

- 批量扫描模型文件夹，自动识别模型类型、版本、大小等信息
- 支持模型备注（描述、笔记、VAE）编辑，自动保存为 JSON
- 支持模型图片（静态/动态预览图）拖拽导入、切换、删除
- 支持模型文件及关联文件（如 json、info、html、图片等）批量移动、重命名、删除及撤销
- 支持 SHA256 哈希值批量生成与查重
- 支持模型查重（按哈希、大小、名称等）
- 支持模型信息导出为 Excel 或 JSON
- 支持模型名称/哈希值模糊搜索，QCompleter 智能提示
- 支持多选批量操作，右键菜单丰富
- 支持撤销上次移动、重命名等操作
- 支持模型图片双击放大查看
- 很多功能自行体验

## 环境依赖

- Python 3.8+
- PySide6
- pandas
- openpyxl
- watchdog
- pywin32（Windows 平台）

安装依赖或运行bat脚本安装requirements（推荐使用虚拟环境）：

```sh
pip install -r requirements.txt
```

或手动安装：

```sh
pip install PySide6 pandas openpyxl watchdog pywin32
```


## 使用方法

1. 运行 `StableDiffusion_ComfyUI_Model_Classifier V1.0.py` 脚本
2. 选择模型目录，点击“扫描模型”
3. 在表格中可进行批量选择、右键操作（移动、重命名、删除、查重等）
4. 右侧可编辑备注、管理预览图，支持拖拽图片
5. 支持导出 Excel/JSON，查重，批量生成 SHA256 等

## 注意事项

- 移动/重命名/删除操作会同步处理模型的所有关联文件（如 json、info、图片等）
- 支持撤销上次的移动、重命名操作
- 预览图支持静态（png/jpg/webp）和动态（gif），支持静态多图切换
- 查重支持哈希、大小、名称等多维度
- 推荐在 Windows 下使用

## 截图

![QQ202576-111414-H20250706114328271D](https://github.com/user-attachments/assets/614c7acb-7d67-4fb1-a39a-44553b22c187)


## 反馈与建议

如有问题或建议，请在本项目 issue 区留言。


# 碎碎念念
- python小白的我！
- py脚本纯100% AI生成的。有些bug可自行修改，目前没有什么影响使用，该有就有的。
- 界面稍微简陋，纯实用主义。
- 一开始直接使用py脚本，坑越挖越大，3000+行代码两眼一抹黑的，新手的我竟然无法进行组件模块化！后悔的肠子都青了！
- 脚本全开源，无任何版权！
