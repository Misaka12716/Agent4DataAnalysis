import os
import json
import csv
import pickle
import pathlib
import pandas as pd
import h5py
import shutil
from datetime import datetime
from typing import List, Dict, Any, Optional, Union


class FileTools:
    """文件处理工具类，提供各类文件读写和系统操作功能

    支持的文件类型包括：
    - 通用文件：文本文件(.txt)、JSON文件(.json)
    - 数据分析文件：CSV(.csv)、Excel(.xlsx/.xls)、Parquet(.parquet)、
                   Pickle(.pkl/.pickle)、HDF5(.h5/.hdf5)

    提供的核心功能：
    - 文件夹操作：列出文件/子文件夹、创建/删除文件夹等
    - 文件操作：复制、移动、删除文件、获取文件信息等
    - 各类文件的读写操作（支持批量读取）
    """

    @staticmethod
    def list_files(folder_path: str, extension: Optional[str] = None) -> List[str]:
        """
        列出目标文件夹中所有文件的完整路径（包括子文件夹中的文件）

        Args:
            folder_path: 字符串类型，目标文件夹的路径（绝对路径或相对路径均可）
            extension: 可选参数，字符串类型，指定文件扩展名（如'.txt'）。
                      若为None（默认），则返回所有类型文件；
                      若指定扩展名（如'.csv'），则只返回该类型文件。

        Returns:
            列表类型，包含所有符合条件的文件的完整路径字符串

        Raises:
            ValueError: 当folder_path不是有效文件夹路径时抛出
        """
        if not os.path.isdir(folder_path):
            raise ValueError(f"路径 {folder_path} 不是有效的文件夹")

        file_paths = []
        # 遍历文件夹及子文件夹
        for root, _, files in os.walk(folder_path):
            for file in files:
                # 根据扩展名筛选文件
                if extension is None or file.endswith(extension):
                    file_paths.append(os.path.join(root, file))
        return file_paths

    @staticmethod
    def list_dirs(folder_path: str) -> List[str]:
        """
        列出目标文件夹中所有直接子文件夹的路径（不包含嵌套子文件夹）

        Args:
            folder_path: 字符串类型，目标文件夹的路径

        Returns:
            列表类型，包含所有直接子文件夹的完整路径字符串

        Raises:
            ValueError: 当folder_path不是有效文件夹路径时抛出
        """
        if not os.path.isdir(folder_path):
            raise ValueError(f"路径 {folder_path} 不是有效的文件夹")

        # 使用scandir高效遍历，只筛选文件夹
        return [entry.path for entry in os.scandir(folder_path) if entry.is_dir()]

    @staticmethod
    def file_exists(file_path: str) -> bool:
        """
        检查指定路径是否为一个存在的文件

        Args:
            file_path: 字符串类型，待检查的文件路径

        Returns:
            布尔类型，True表示路径是存在的文件，False表示不存在或不是文件
        """
        return os.path.isfile(file_path)

    @staticmethod
    def dir_exists(folder_path: str) -> bool:
        """
        检查指定路径是否为一个存在的文件夹

        Args:
            folder_path: 字符串类型，待检查的文件夹路径

        Returns:
            布尔类型，True表示路径是存在的文件夹，False表示不存在或不是文件夹
        """
        return os.path.isdir(folder_path)

    @staticmethod
    def create_dir(folder_path: str, exist_ok: bool = True) -> None:
        """
        创建文件夹（支持递归创建多级目录）

        Args:
            folder_path: 字符串类型，要创建的文件夹路径（可包含多级目录）
            exist_ok: 布尔类型，默认True。若为True，当文件夹已存在时不报错；
                      若为False，当文件夹已存在时会抛出FileExistsError

        Returns:
            无返回值
        """
        os.makedirs(folder_path, exist_ok=exist_ok)

    @staticmethod
    def delete_file(file_path: str) -> None:
        """
        删除指定文件（仅删除文件，不删除文件夹）

        Args:
            file_path: 字符串类型，要删除的文件路径

        Returns:
            无返回值（若文件不存在则不执行操作）
        """
        if os.path.isfile(file_path):
            os.remove(file_path)

    @staticmethod
    def delete_dir(folder_path: str, recursive: bool = False) -> None:
        """
        删除指定文件夹

        Args:
            folder_path: 字符串类型，要删除的文件夹路径
            recursive: 布尔类型，默认False。若为False，仅删除空文件夹；
                      若为True，递归删除文件夹及其中所有内容（包括子文件夹和文件）

        Returns:
            无返回值（若文件夹不存在则不执行操作）

        Raises:
            OSError: 当recursive=False且文件夹非空时抛出
        """
        if os.path.isdir(folder_path):
            if recursive:
                shutil.rmtree(folder_path)  # 递归删除
            else:
                os.rmdir(folder_path)  # 仅删除空文件夹

    @staticmethod
    def copy_file(src_path: str, dest_path: str) -> None:
        """
        复制文件（保留文件元数据，如创建时间、修改时间）

        Args:
            src_path: 字符串类型，源文件路径（必须是存在的文件）
            dest_path: 字符串类型，目标路径。若目标是文件夹，则文件会复制到该文件夹下；
                      若目标是文件，则会以该名称保存（若已存在则覆盖）

        Returns:
            无返回值（若源文件不存在则不执行操作）
        """
        if os.path.isfile(src_path):
            shutil.copy2(src_path, dest_path)  # copy2保留元数据

    @staticmethod
    def move_file(src_path: str, dest_path: str) -> None:
        """
        移动文件（可实现文件重命名功能）

        Args:
            src_path: 字符串类型，源文件路径（必须是存在的文件）
            dest_path: 字符串类型，目标路径。若目标是文件夹，则文件会移动到该文件夹下；
                      若目标是文件，则会以该名称保存（若已存在则覆盖）

        Returns:
            无返回值（若源文件不存在则不执行操作）
        """
        if os.path.isfile(src_path):
            shutil.move(src_path, dest_path)

    # 文本文件处理
    @staticmethod
    def read_text(file_path: str, encoding: str = "utf-8") -> str:
        """
        读取文本文件内容（支持指定编码）

        Args:
            file_path: 字符串类型，文本文件路径（.txt等文本格式）
            encoding: 字符串类型，默认'utf-8'，文件编码格式（如'gbk'、'utf-16'等）

        Returns:
            字符串类型，文件的全部内容

        Raises:
            FileNotFoundError: 当file_path不存在时抛出
            UnicodeDecodeError: 当编码格式不匹配时抛出
        """
        with open(file_path, "r", encoding=encoding) as f:
            return f.read()

    @staticmethod
    def write_text(
        file_path: str, content: str, encoding: str = "utf-8", overwrite: bool = True
    ) -> None:
        """
        将字符串写入文本文件

        Args:
            file_path: 字符串类型，要写入的文件路径（会自动创建父目录）
            content: 字符串类型，要写入的文本内容
            encoding: 字符串类型，默认'utf-8'，文件编码格式
            overwrite: 布尔类型，默认True。若为True，当文件已存在时覆盖；
                      若为False，当文件已存在时抛出FileExistsError

        Returns:
            无返回值
        """
        if not overwrite and os.path.exists(file_path):
            raise FileExistsError(f"文件 {file_path} 已存在，且overwrite设为False")

        # 确保父目录存在（若不存在则创建）
        dir_path = os.path.dirname(file_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

        with open(file_path, "w", encoding=encoding) as f:
            f.write(content)

    # JSON文件处理
    @staticmethod
    def read_json(file_path: str, encoding: str = "utf-8") -> Union[Dict, List]:
        """
        读取JSON文件并解析为Python对象（字典或列表）

        Args:
            file_path: 字符串类型，JSON文件路径（.json）
            encoding: 字符串类型，默认'utf-8'，文件编码格式

        Returns:
            字典或列表类型，JSON解析后的Python对象

        Raises:
            FileNotFoundError: 当file_path不存在时抛出
            json.JSONDecodeError: 当文件内容不是合法JSON格式时抛出
        """
        with open(file_path, "r", encoding=encoding) as f:
            return json.load(f)

    @staticmethod
    def write_json(
        file_path: str,
        data: Union[Dict, List],
        encoding: str = "utf-8",
        indent: int = 4,
        overwrite: bool = True,
    ) -> None:
        """
        将Python对象（字典或列表）写入JSON文件

        Args:
            file_path: 字符串类型，要写入的JSON文件路径（会自动创建父目录）
            data: 字典或列表类型，要写入的Python对象（必须是JSON可序列化类型）
            encoding: 字符串类型，默认'utf-8'，文件编码格式
            indent: 整数类型，默认4，JSON格式化缩进空格数（0表示不缩进）
            overwrite: 布尔类型，默认True。若为True，当文件已存在时覆盖；
                      若为False，当文件已存在时抛出FileExistsError

        Returns:
            无返回值

        Raises:
            TypeError: 当data包含不可JSON序列化的类型时抛出
        """
        if not overwrite and os.path.exists(file_path):
            raise FileExistsError(f"文件 {file_path} 已存在，且overwrite设为False")

        dir_path = os.path.dirname(file_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

        with open(file_path, "w", encoding=encoding) as f:
            # ensure_ascii=False：保留非ASCII字符（如中文）的原始格式
            json.dump(data, f, indent=indent, ensure_ascii=False)

    # CSV文件处理（基于pandas）
    @staticmethod
    def read_csv(file_path: str, **kwargs) -> pd.DataFrame:
        """
        读取CSV文件并返回pandas DataFrame

        Args:
            file_path: 字符串类型，CSV文件路径（.csv）
           ** kwargs: 可变参数，传递给pandas.read_csv的所有参数。例如：
                      - sep: 分隔符（默认','）
                      - header: 表头行索引（默认0，即第一行为表头）
                      - index_col: 作为行索引的列（默认None）
                      - usecols: 需要读取的列（默认所有列）
                      - dtype: 指定列的数据类型（如{'id': str}）

        Returns:
            pandas.DataFrame类型，CSV文件解析后的表格数据
        """
        return pd.read_csv(file_path, **kwargs)

    @staticmethod
    def write_csv(file_path: str, data: pd.DataFrame, **kwargs) -> None:
        """
        将pandas DataFrame写入CSV文件

        Args:
            file_path: 字符串类型，要写入的CSV文件路径（会自动创建父目录）
            data: pandas.DataFrame类型，要写入的表格数据
            **kwargs: 可变参数，传递给pandas.DataFrame.to_csv的所有参数。例如：
                      - index: 是否保存行索引（默认True，建议设为False）
                      - sep: 分隔符（默认','）
                      - header: 是否保存表头（默认True）
                      - encoding: 编码格式（默认'utf-8'）

        Returns:
            无返回值
        """
        dir_path = os.path.dirname(file_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

        data.to_csv(file_path, **kwargs)

    # Excel文件处理（基于pandas）
    @staticmethod
    def read_excel(file_path: str, **kwargs) -> pd.DataFrame:
        """
        读取Excel文件（.xlsx/.xls）并返回pandas DataFrame

        Args:
            file_path: 字符串类型，Excel文件路径
            **kwargs: 可变参数，传递给pandas.read_excel的所有参数。例如：
                      - sheet_name: 要读取的工作表名称或索引（默认0）
                      - header: 表头行索引（默认0）
                      - usecols: 需要读取的列（如'A:C'或[0,1,2]）
                      - skiprows: 需要跳过的行数（默认None）

        Returns:
            pandas.DataFrame类型，Excel文件解析后的表格数据
        """
        return pd.read_excel(file_path, **kwargs)

    @staticmethod
    def write_excel(file_path: str, data: pd.DataFrame, **kwargs) -> None:
        """
        将pandas DataFrame写入Excel文件

        Args:
            file_path: 字符串类型，要写入的Excel文件路径（会自动创建父目录）
            data: pandas.DataFrame类型，要写入的表格数据
            **kwargs: 可变参数，传递给pandas.DataFrame.to_excel的所有参数。例如：
                      - sheet_name: 工作表名称（默认'Sheet1'）
                      - index: 是否保存行索引（默认True，建议设为False）
                      - header: 是否保存表头（默认True）
                      - startrow/startcol: 开始写入的行/列索引（默认0）

        Returns:
            无返回值
        """
        dir_path = os.path.dirname(file_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

        data.to_excel(file_path, **kwargs)

    # Parquet文件处理（基于pandas，高效列式存储）
    @staticmethod
    def read_parquet(file_path: str, **kwargs) -> pd.DataFrame:
        """
        读取Parquet文件并返回pandas DataFrame（适合大数据存储）

        Args:
            file_path: 字符串类型，Parquet文件路径（.parquet）
            **kwargs: 可变参数，传递给pandas.read_parquet的所有参数。例如：
                      - columns: 需要读取的列（默认所有列）
                      - engine: 解析引擎（默认'pyarrow'）

        Returns:
            pandas.DataFrame类型，Parquet文件解析后的表格数据
        """
        return pd.read_parquet(file_path, **kwargs)

    @staticmethod
    def write_parquet(file_path: str, data: pd.DataFrame, **kwargs) -> None:
        """
        将pandas DataFrame写入Parquet文件（压缩率高，读写快）

        Args:
            file_path: 字符串类型，要写入的Parquet文件路径（会自动创建父目录）
            data: pandas.DataFrame类型，要写入的表格数据
            **kwargs: 可变参数，传递给pandas.DataFrame.to_parquet的所有参数。例如：
                      - engine: 写入引擎（默认'pyarrow'）
                      - compression: 压缩方式（如'snappy'、'gzip'，默认'snappy'）

        Returns:
            无返回值
        """
        dir_path = os.path.dirname(file_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

        data.to_parquet(file_path, **kwargs)

    # Pickle文件处理（Python对象序列化）
    @staticmethod
    def read_pickle(file_path: str, **kwargs) -> Any:
        """
        读取Pickle文件并反序列化为Python对象（支持任意Python对象）

        Args:
            file_path: 字符串类型，Pickle文件路径（.pkl/.pickle）
            **kwargs: 可变参数，传递给pickle.load的参数（如encoding指定反序列化编码）

        Returns:
            任意Python对象，反序列化后的原始对象

        Raises:
            pickle.UnpicklingError: 当文件不是合法Pickle格式时抛出
        """
        with open(file_path, "rb") as f:  # 二进制读取
            return pickle.load(f, **kwargs)

    @staticmethod
    def write_pickle(file_path: str, data: Any, **kwargs) -> None:
        """
        将Python对象序列化并写入Pickle文件（适合临时存储复杂对象）

        Args:
            file_path: 字符串类型，要写入的Pickle文件路径（会自动创建父目录）
            data: 任意Python对象，要序列化的对象（几乎支持所有Python类型）
            **kwargs: 可变参数，传递给pickle.dump的参数。例如：
                      - protocol: 序列化协议版本（默认最高版本）

        Returns:
            无返回值
        """
        dir_path = os.path.dirname(file_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

        with open(file_path, "wb") as f:  # 二进制写入
            pickle.dump(data, f, **kwargs)

    # HDF5文件处理（适合存储大型数值数据）
    @staticmethod
    def read_hdf5(file_path: str, key: Optional[str] = None) -> Any:
        """
        读取HDF5文件中的数据（适合存储多维数组等大型数值数据）

        Args:
            file_path: 字符串类型，HDF5文件路径（.h5/.hdf5）
            key: 可选参数，字符串类型。HDF5文件中数据集的键名：
                 - 若指定key，则返回该键对应的数据集（如数组）；
                 - 若为None（默认），则返回整个HDF5文件对象（需手动关闭）

        Returns:
            若指定key，返回数据集（如numpy数组）；否则返回h5py.File对象

        Raises:
            KeyError: 当指定的key在HDF5文件中不存在时抛出
        """
        with h5py.File(file_path, "r") as f:  # 只读模式打开
            if key is not None:
                return f[key][()]  # 返回数据集的副本
            return f  # 返回文件对象（需注意：with块外使用需手动管理生命周期）

    @staticmethod
    def write_hdf5(file_path: str, data: Any, key: str = "data", **kwargs) -> None:
        """
        将数据写入HDF5文件（适合存储大型数值数据，如numpy数组）

        Args:
            file_path: 字符串类型，要写入的HDF5文件路径（会自动创建父目录）
            data: 数值型数据（如numpy数组、列表等），要存储的数据
            key: 字符串类型，默认'data'，存储数据集的键名（用于后续读取）
           ** kwargs: 可变参数，传递给h5py.File.create_dataset的参数。例如：
                      - dtype: 数据类型（默认自动推断）
                      - compression: 压缩方式（如'gzip'）

        Returns:
            无返回值
        """
        dir_path = os.path.dirname(file_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

        with h5py.File(file_path, "w") as f:  # 写入模式（会覆盖已有文件）
            f.create_dataset(key, data=data, **kwargs)

    # 辅助功能：文件信息查询
    @staticmethod
    def get_file_size(file_path: str, human_readable: bool = False) -> Union[int, str]:
        """
        获取文件大小

        Args:
            file_path: 字符串类型，目标文件路径
            human_readable: 布尔类型，默认False。若为True，返回人类可读格式（如'1.23 MB'）；
                           若为False，返回字节数（整数）

        Returns:
            若human_readable=True，返回字符串（带单位的大小）；
            否则返回整数（字节数）

        Raises:
            ValueError: 当file_path不是有效文件时抛出
        """
        if not os.path.isfile(file_path):
            raise ValueError(f"路径 {file_path} 不是有效的文件")

        size_bytes = os.path.getsize(file_path)

        if not human_readable:
            return size_bytes

        # 转换为人类可读格式（B -> KB -> MB -> ...）
        units = ["B", "KB", "MB", "GB", "TB"]
        size = size_bytes
        unit_index = 0

        # 单位转换（1024进制）
        while size >= 1024 and unit_index < len(units) - 1:
            size /= 1024
            unit_index += 1

        return f"{size:.2f} {units[unit_index]}"

    @staticmethod
    def get_file_info(file_path: str) -> Dict[str, Any]:
        """
        获取文件的详细信息（路径、大小、时间等）

        Args:
            file_path: 字符串类型，目标文件或文件夹的路径

        Returns:
            字典类型，包含以下信息：
            - 'path': 完整路径
            - 'name': 文件名（含扩展名）
            - 'size': 大小（字节数）
            - 'size_human': 人类可读的大小
            - 'is_file': 是否为文件（布尔值）
            - 'is_dir': 是否为文件夹（布尔值）
            - 'created_time': 创建时间（datetime对象）
            - 'modified_time': 最后修改时间（datetime对象）
            - 'accessed_time': 最后访问时间（datetime对象）
            - 'extension': 文件扩展名（如'.txt'，文件夹为''）

        Raises:
            ValueError: 当file_path不存在时抛出
        """
        if not os.path.exists(file_path):
            raise ValueError(f"路径 {file_path} 不存在")

        stat_info = os.stat(file_path)  # 获取文件系统信息

        return {
            "path": file_path,
            "name": os.path.basename(file_path),
            "size": FileTools.get_file_size(file_path),
            "size_human": FileTools.get_file_size(file_path, human_readable=True),
            "is_file": os.path.isfile(file_path),
            "is_dir": os.path.isdir(file_path),
            "created_time": datetime.fromtimestamp(stat_info.st_ctime),  # 创建时间
            "modified_time": datetime.fromtimestamp(stat_info.st_mtime),  # 修改时间
            "accessed_time": datetime.fromtimestamp(stat_info.st_atime),  # 访问时间
            "extension": pathlib.Path(file_path).suffix,  # 扩展名
        }

    @staticmethod
    def batch_read_files(folder_path: str, file_type: str) -> Dict[str, Any]:
        """
        批量读取文件夹中指定类型的所有文件，返回文件名到内容的映射

        Args:
            folder_path: 字符串类型，目标文件夹路径
            file_type: 字符串类型，文件类型（支持'txt'、'json'、'csv'、'xlsx'、
                      'parquet'、'pkl'、'h5'等，带不带点均可，如'txt'或'.txt'）

        Returns:
            字典类型，键为文件名（含扩展名），值为文件内容（根据文件类型不同而不同）

        Raises:
            ValueError: 当file_type不支持时抛出
        """
        # 统一扩展名格式（确保以点开头）
        file_extension = f".{file_type}" if not file_type.startswith(".") else file_type
        # 获取该类型的所有文件
        files = FileTools.list_files(folder_path, file_extension)

        result = {}
        read_method = None

        # 根据文件类型匹配对应的读取方法
        if file_type in ["txt", ".txt"]:
            read_method = FileTools.read_text
        elif file_type in ["json", ".json"]:
            read_method = FileTools.read_json
        elif file_type in ["csv", ".csv"]:
            read_method = FileTools.read_csv
        elif file_type in ["xlsx", "xls", ".xlsx", ".xls"]:
            read_method = FileTools.read_excel
        elif file_type in ["parquet", ".parquet"]:
            read_method = FileTools.read_parquet
        elif file_type in ["pkl", "pickle", ".pkl", ".pickle"]:
            read_method = FileTools.read_pickle
        elif file_type in ["h5", "hdf5", ".h5", ".hdf5"]:
            read_method = FileTools.read_hdf5
        else:
            raise ValueError(f"不支持的文件类型: {file_type}")

        # 批量读取文件
        for file_path in files:
            file_name = os.path.basename(file_path)
            try:
                result[file_name] = read_method(file_path)
            except Exception as e:
                print(f"读取文件 {file_path} 时出错: {str(e)}")

        return result


# 示例用法（运行脚本时执行）
if __name__ == "__main__":
    # 创建工具类实例（静态方法也可直接通过类调用，如FileTools.read_text()）
    ft = FileTools()

    # 1. 创建测试文件夹
    test_dir = "test_files"
    ft.create_dir(test_dir)  # 若文件夹已存在，因exist_ok=True不会报错
    print(f"已创建测试文件夹: {test_dir}")

    # 2. 文本文件操作
    txt_path = os.path.join(test_dir, "test.txt")
    ft.write_text(txt_path, "这是一个测试文本文件\n第二行内容")  # 写入文本
    txt_content = ft.read_text(txt_path)  # 读取文本
    print(f"\n文本文件内容:\n{txt_content}")

    # 3. JSON文件操作
    json_path = os.path.join(test_dir, "test.json")
    json_data = {"name": "测试", "value": 123, "list": [1, 2, 3]}
    ft.write_json(json_path, json_data)  # 写入JSON
    read_json = ft.read_json(json_path)  # 读取JSON
    print(f"\nJSON文件内容: {read_json}")

    # 4. CSV文件操作（需pandas）
    csv_path = os.path.join(test_dir, "test.csv")
    df = pd.DataFrame({"A": [1, 2, 3], "B": ["a", "b", "c"], "C": [True, False, True]})
    ft.write_csv(csv_path, df, index=False)  # 写入CSV（不保存索引）
    read_csv = ft.read_csv(csv_path)  # 读取CSV
    print(f"\nCSV文件内容:\n{read_csv}")

    # 5. 获取文件信息
    file_info = ft.get_file_info(txt_path)
    print(f"\n文件信息:")
    for key, value in file_info.items():
        print(f"  {key}: {value}")

    # 6. 列出文件夹中的所有文件
    all_files = ft.list_files(test_dir)
    print(f"\n测试文件夹中的所有文件: {all_files}")

    # 7. 批量读取文件夹中的JSON文件
    json_files = ft.batch_read_files(test_dir, "json")
    print(f"\n批量读取的JSON文件: {json_files.keys()}")

    # 8. 清理测试文件（递归删除整个文件夹）
    ft.delete_dir(test_dir, recursive=True)
    print("\n测试完成，已清理测试文件")
