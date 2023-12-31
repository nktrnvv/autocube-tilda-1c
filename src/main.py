from pathlib import Path
from typing import Sequence

from loguru import logger

from src.config import settings
from src.entities import Folder, Product
from src.images import DropboxImages, ImagesFolder
from src.odata_1c import OData1CClient, OData1CMapper
from src.state import State
from src.tilda import TildaCsvFileManager, TildaSeleniumCsvFileUploader

logger.add(
    settings.logfile, format="{time} {level} {message}", level="INFO",
    rotation="10 MB"
)


def get_product_brand(folders: Sequence[Folder]) -> str:
    for folder in folders:
        if folder.nesting_level == 1:
            return folder.name
    return ""


def get_product_price(product: dict) -> int | None:
    for price_option in product["Цены"]:
        if price_option["ТипЦены"][0]["Description"] == "Розничная цена":
            return int(price_option["Цена"])


def get_product_quantity(product: dict) -> int:
    if product["Остаток"]:
        return int(product["Остаток"][0]["КоличествоBalance"])
    return 0


def map_single_product(
        product: dict, folders: Sequence[Folder]) -> Product | None:
    sku = product["Артикул"]
    if not sku:
        return

    folder_names = [folder.name for folder in folders]

    is_part = "Запасные части" in folder_names
    is_foton = "FOTON" in folder_names
    is_ashok = "ASHOK" in folder_names

    if not (is_part and (is_foton or is_ashok)):
        return

    quantity = get_product_quantity(product)
    description = "В наличии"
    if quantity == 0:
        description = "На заказ"

    title = product["НаименованиеПолное"].replace(",", ", ")
    title = f"{title} [арт. {sku}]"

    return Product(
        external_id=product["Ref_Key"],
        title=title,
        sku=sku,
        brand=get_product_brand(folders),
        description=description,
        price=get_product_price(product),
        categories=[
            "Запчасти/Каталог", f"Запчасти/{get_product_brand(folders)}"]
    )


def get_products_from_1c() -> Sequence[Product]:
    odata_client = OData1CClient(
        settings.odata_url, settings.odata_username, settings.odata_password)

    product_entities = odata_client.get_entities(
        entity_name="Catalog_Номенклатура",
        select=[
            "Ref_Key", "Parent_Key", "IsFolder", "Description",
            "НаименованиеПолное", "Артикул"
        ]
    )

    stock_entities = odata_client.get_entities(
        entity_name="AccumulationRegister_ОстаткиТоваровКомпании/Balance()",
        select=["Номенклатура_Key", "КоличествоBalance"]
    )

    price_entities = odata_client.get_entities(
        entity_name="InformationRegister_Цены_RecordType/SliceLast()",
        select=["Номенклатура_Key", "ТипЦен_Key", "Цена"]
    )

    price_type_entities = odata_client.get_entities(
        entity_name="Catalog_ТипыЦен", select=["Ref_Key", "Description"]
    )

    prices_extended_with_types = price_entities.expand_with(
        price_type_entities,
        expand_condition=lambda price, price_type:
            price["ТипЦен_Key"] == price_type["Ref_Key"],
        key="ТипЦены"
    )

    extended_product_entities = product_entities.expand_with(
        prices_extended_with_types,
        expand_condition=lambda product, price:
            product["Ref_Key"] == price["Номенклатура_Key"],
        key="Цены"
    ).expand_with(
        stock_entities,
        expand_condition=lambda product, stock:
            product["Ref_Key"] == stock["Номенклатура_Key"],
        key="Остаток"
    )

    mapped_products = OData1CMapper(
        extended_product_entities, map_single_product).map_products()

    logger.info(f"Successfully received information about "
                f"{len(mapped_products)} products using OData.")

    if len(mapped_products) > settings.max_products_number:
        logger.warning(f"Exceeded the maximum number of products in the Tilda. "
                       f"The number of products was limited to "
                       f"{settings.max_products_number}")

    return mapped_products[:settings.max_products_number]


def upload_products_to_tilda(products: Sequence[Product]):
    if len(products) == 0:
        logger.info("Uploading files to the Tilde is skipped because the "
                    "number of products is zero")
        return

    file_manager = TildaCsvFileManager(
        settings.csv_files_directory,
        filename_format="import_{datetime}.csv",
        products=products
    )
    file_manager.create_file()

    file_uploader = TildaSeleniumCsvFileUploader(
        file_manager.filepath,
        settings.tilda_email,
        settings.tilda_password,
        settings.tilda_project_id,
        settings.selenium_timeout,
        settings.selenium_file_uploading_timeout
    )
    file_uploader.upload_file()


@logger.catch
def main():
    products = get_products_from_1c()

    images_folder = ImagesFolder(
        settings.images_folder,
        products,
        settings.default_image,
        match=lambda product, image_name: product.sku == image_name,
    )
    products_with_images = images_folder.get_products_with_images()

    state = State(settings.state_file)
    products_with_images_to_update = state.filter_not_presented(
        products_with_images)

    dropbox_images = DropboxImages(
        settings.dropbox_refresh_token,
        settings.dropbox_app_key,
        settings.dropbox_app_secret,
        "/Запчасти",
        products_with_images_to_update,
        Path(settings.images_folder) / settings.default_image
    )
    products_with_image_urls = dropbox_images.get_products_with_image_urls()

    upload_products_to_tilda(products_with_image_urls)

    state.dump(products_with_images)
    dropbox_images.delete_uploaded_images()


if __name__ == '__main__':
    main()
